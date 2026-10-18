"""Responses request admission and transport orchestration."""

from __future__ import annotations

import time
import urllib.error
from http.server import BaseHTTPRequestHandler

from openai_responses_proxy.protocol import agent_delivery
from openai_responses_proxy.protocol import response as replay_response
from openai_responses_proxy.protocol.replay import projection as replay_request
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay.upstream import header_value
from openai_responses_proxy.runtime import config as runtime_config

_SETTINGS = runtime_config.load()


def resolve_upstream(
    path: str, providers: provider_registry.Registry
) -> tuple[str, str, str] | None:
    """Resolve one configured provider namespace without provider branching."""
    resolved = providers.resolve(path)
    return None if resolved is None else (resolved[0].name, resolved[1], resolved[2])


def _admit(exchange: upstream_exchange.Exchange) -> bool:
    if not exchange.is_responses:
        return True
    verdict, active_now = admission.admit_response()
    if verdict == "draining":
        telemetry.record_counter("responses_rejected_while_draining")
        telemetry.record_failure("draining")
        downstream.send_payload(
            exchange.handler,
            503,
            replay_response.error_payload(
                "Proxy is draining active Responses; retry the turn shortly",
                "server_busy",
                "proxy_draining",
            ),
            retry_after="1",
        )
        exchange.log("responses_rejected_while_draining")
        return False
    exchange.log("responses_admitted", f"active={active_now} ")
    return True


def _cooldown_active(exchange: upstream_exchange.Exchange) -> bool:
    """Reject one Responses request while its provider policy is cooling down."""
    if not exchange.is_responses:
        return False
    policy = exchange.profile.wire_policy
    if policy is not None:
        fingerprint = policy.request_fingerprint(exchange.body)
        remaining = cooldown.remaining(fingerprint)
        if remaining > 0:
            telemetry.record_counter("wire_failure_cooldown_hits")
            telemetry.record_failure("wire_failure_cooldown_hit")
            downstream.send_wire_failure_exhausted(exchange.handler, policy, 0)
            exchange.log("wire_failure_cooldown_hit", f"remaining_seconds={remaining:.1f} ")
            return True
    remaining = cooldown.remaining(cooldown.unavailable_key(exchange.profile.name))
    if remaining > 0:
        seconds = max(1, int(remaining + 0.999))
        telemetry.record_counter("provider_unavailable_cooldown_hits")
        telemetry.record_failure("provider_unavailable_cooldown")
        downstream.send_payload(
            exchange.handler,
            503,
            replay_response.error_payload(
                "provider is temporarily unavailable; retry the turn shortly",
                "server_busy",
                "provider_unavailable_cooldown",
            ),
            retry_after=str(seconds),
        )
        exchange.log("provider_unavailable_cooldown", f"remaining_seconds={seconds} ")
        return True
    remaining = cooldown.remaining(cooldown.provider_key(exchange.profile.name))
    if remaining <= 0:
        return False
    seconds = max(1, int(remaining + 0.999))
    telemetry.record_counter("provider_rate_limit_cooldown_hits")
    telemetry.record_failure("provider_rate_limit_cooldown")
    downstream.send_payload(
        exchange.handler,
        429,
        replay_response.error_payload(
            "provider rate limit cooldown is active; retry the turn shortly",
            "rate_limit_error",
            "provider_rate_limit_cooldown",
        ),
        retry_after=str(seconds),
    )
    exchange.log("provider_rate_limit_cooldown", f"remaining_seconds={seconds} ")
    return True


def _reject_route(
    handler: BaseHTTPRequestHandler, request_id: int, method: str | None = None
) -> None:
    """Reject one unsupported route without retaining its unread request body."""
    telemetry.record_counter("provider_route_rejected")
    telemetry.record_failure("provider_route_rejected")
    code = "provider_route_not_found" if method is None else "provider_route_method_not_allowed"
    message = (
        "request path is not a configured provider route"
        if method is None
        else "request method is not supported for the configured provider route"
    )
    downstream.send_payload(
        handler,
        404,
        replay_response.error_payload(message, "invalid_request_error", code),
        close=True,
    )
    event = "provider_route_rejected" if method is None else "provider_route_method_rejected"
    detail = "" if method is None else f"method={method} "
    operational_log.log(
        f"req={request_id} event={event} {detail}"
        f"path={operational_log.safe_request_path(handler.path)}"
    )


def _request_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Return the shared upstream header projection for supported routes."""
    headers = {
        name: value
        for name, value in handler.headers.items()
        if name.lower() not in downstream.HOP_BY_HOP
    }
    headers["Accept-Encoding"] = "identity"
    return headers


def _read_responses_body(handler: BaseHTTPRequestHandler) -> tuple[bytes | None, str]:
    """Read one unambiguous length-framed body before protocol or upstream admission."""
    if handler.headers.get_all("Transfer-Encoding"):
        return None, "unsupported_transfer_encoding"
    lengths = handler.headers.get_all("Content-Length", [])
    if not lengths:
        return None, "missing_content_length"
    if len(lengths) != 1:
        return None, "ambiguous_content_length"
    value = lengths[0].strip()
    if not value.isascii() or not value.isdecimal():
        return None, "invalid_content_length"
    try:
        length = int(value)
    except (ValueError, OverflowError):
        return None, "invalid_content_length"
    if length > _SETTINGS.request_body_max_bytes:
        return None, "request_body_too_large"
    return _read_bounded_body(handler, length)


def _read_bounded_body(handler: BaseHTTPRequestHandler, length: int) -> tuple[bytes | None, str]:
    """Spend one body deadline without reading beyond the declared frame."""
    deadline = time.monotonic() + _SETTINGS.request_body_timeout
    connection = handler.connection
    previous_timeout = connection.gettimeout()
    body = bytearray()
    try:
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "request_body_timeout"
            connection.settimeout(
                remaining if previous_timeout is None else min(previous_timeout, remaining)
            )
            chunk = handler.rfile.read1(min(length - len(body), 64 * 1024))
            if not chunk:
                return None, "incomplete_request_body"
            body.extend(chunk)
        if time.monotonic() >= deadline:
            return None, "request_body_timeout"
    except TimeoutError:
        return None, "request_body_timeout"
    except OSError:
        return None, "request_body_unreadable"
    finally:
        connection.settimeout(previous_timeout)
    return bytes(body), ""


def _relay_catalog(
    handler: BaseHTTPRequestHandler,
    request_id: int,
    profile: provider_registry.Profile,
    upstream_url: str,
    headers: dict[str, str],
) -> None:
    """Relay one model catalog read exactly once without Responses policy."""
    try:
        response = upstream_exchange.open_readonly(upstream_url, "GET", headers)
    except urllib.error.HTTPError as error:
        try:
            payload, status, upstream_headers = error.read(), error.code, error.headers
        finally:
            error.close()
        downstream.relay_error(handler, status, upstream_headers, payload)
        telemetry.record_failure(f"catalog_http_{status}")
        operational_log.log(
            f"req={request_id} event=catalog_http_terminal provider={profile.name} "
            f"status={status} response_bytes={len(payload)} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )
        return
    except Exception as error:
        telemetry.record_failure("catalog_transport_error")
        downstream.send_payload(
            handler,
            502,
            replay_response.error_payload(
                "Upstream model catalog transport failed; retry discovery shortly",
                "upstream_unavailable",
                "catalog_transport_error",
            ),
        )
        operational_log.log(
            f"req={request_id} event=catalog_transport_error provider={profile.name} "
            f"{operational_log.safe_exception_context(error)} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )
        return
    downstream.relay_readonly_body(handler, response)


def relay(
    handler: BaseHTTPRequestHandler,
    method: str,
    providers: provider_registry.Registry,
) -> None:
    """Relay one downstream request through bounded compatibility policies."""
    request_id = admission.next_request_id()
    resolved = resolve_upstream(handler.path, providers)
    if resolved is None:
        _reject_route(handler, request_id)
        return
    route, resource, upstream_url = resolved
    profile = providers.profiles[route]
    is_responses = method == "POST" and resource == "responses"
    is_catalog = method == "GET" and resource == "models"
    if not (is_responses or is_catalog):
        _reject_route(handler, request_id, method)
        return
    headers = _request_headers(handler)
    if is_catalog:
        _relay_catalog(handler, request_id, profile, upstream_url, headers)
        return
    body, framing_reason = _read_responses_body(handler)
    if body is None:
        status, code = 400, "request_framing_rejected"
        match framing_reason:
            case "request_body_too_large":
                status, code = 413, "request_body_rejected"
            case "request_body_timeout":
                status, code = 408, "request_body_rejected"
            case "request_body_unreadable":
                code = "request_body_rejected"
        downstream.send_payload(
            handler,
            status,
            replay_response.error_payload(
                "Responses request body admission failed",
                "invalid_request_error",
                code,
                reason=framing_reason,
            ),
            close=True,
        )
        telemetry.record_counter(code)
        telemetry.record_failure(code)
        operational_log.log(
            f"req={request_id} event={code} provider={profile.name} "
            f"reason={framing_reason} path={operational_log.safe_request_path(handler.path)}"
        )
        return
    projection = replay_request.sanitize_responses_body(body)
    note = projection.diagnostic()
    telemetry.record_sanitization(projection.metrics)
    if projection.body is None:
        reason = projection.reason or "unknown"
        telemetry.record_counter("provider_portable_projection_rejected")
        telemetry.record_failure("provider_portable_projection_rejected")
        downstream.send_payload(
            handler,
            400,
            replay_response.error_payload(
                "Responses replay contains an unproved provider-portable structure",
                "invalid_request_error",
                "provider_portable_projection_rejected",
                reason=reason,
            ),
        )
        operational_log.log(
            f"req={request_id} event=provider_portable_projection_rejected "
            f"provider={profile.name} reason={reason} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )
        return
    body = projection.body
    try:
        prepared = agent_delivery.prepare_request(body, profile.agent_message_delivery)
    except ValueError as error:
        downstream.send_payload(
            handler,
            400,
            replay_response.error_payload(
                "Responses delegation does not match the selected delivery policy",
                "invalid_request_error",
                "agent_delivery_rejected",
                reason=str(error),
            ),
        )
        telemetry.record_counter("agent_delivery_rejected")
        return
    body = prepared.body
    if len(body) >= 400_000:
        path = operational_log.safe_request_path(handler.path)
        operational_log.log(
            f"req={request_id} event=large_request provider={profile.name} "
            f"bytes={len(body)} path={path}"
        )
    path = operational_log.safe_request_path(handler.path)
    operational_log.log(
        f"req={request_id} event=request_sanitized provider={profile.name} "
        f"method={method} {note} path={path}"
    )
    telemetry.record_counter("responses_received")
    exchange = upstream_exchange.Exchange(
        handler,
        method,
        request_id,
        body,
        upstream_url,
        headers,
        is_responses,
        body,
        profile,
        plaintext_agent_delivery=prepared.active,
        input_dependencies=projection.input_dependencies,
    )
    if _cooldown_active(exchange) or not _admit(exchange):
        return
    try:
        response = upstream_exchange.open_upstream(exchange)
        if response is None:
            return
        content_type = header_value(response.headers, "Content-Type")
        if "text/event-stream" in content_type.lower():
            downstream.relay_sse(exchange, response)
        else:
            downstream.relay_responses_json(exchange, response)
    finally:
        active_now = admission.release_response()
        exchange.log("responses_released", f"active={active_now} ")
