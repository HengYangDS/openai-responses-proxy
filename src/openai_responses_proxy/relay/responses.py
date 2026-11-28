"""Responses request admission and transport orchestration."""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING

from openai_responses_proxy.protocol import response as replay_response
from openai_responses_proxy.protocol.replay import projection as replay_request
from openai_responses_proxy.relay import addressing
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay.upstream import ExchangeDeadlineError
from openai_responses_proxy.relay.upstream import header_value
from openai_responses_proxy.runtime import config as runtime_config

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_SETTINGS = runtime_config.load()
# A request body at least this large is logged as a large request.
_LARGE_REQUEST_BYTES = 400_000
# Any web page can send the local-client refusal at will, so its log line covers an interval.
LOCAL_CLIENT_REFUSAL_LOG_SECONDS = 60.0
_LOCAL_CLIENT_REFUSAL_LOG = operational_log.Throttle(LOCAL_CLIENT_REFUSAL_LOG_SECONDS)


def _cooldown_active(exchange: upstream_exchange.Exchange) -> bool:
    """Reject one Responses request while its upstream origin is cooling down."""
    remaining = cooldown.remaining(cooldown.unavailable_key(exchange.target.origin))
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
    remaining = cooldown.remaining(cooldown.rate_limit_key(exchange.target.origin))
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


def _send_refusal(handler: BaseHTTPRequestHandler, status: int, code: str, message: str) -> None:
    """Count one local refusal under its error code and answer it without reading the body.

    The response closes the connection, so an unread body is never parsed as a request.
    """
    telemetry.record_counter(code)
    downstream.send_payload(
        handler,
        status,
        replay_response.error_payload(message, "invalid_request_error", code),
        close=True,
    )


def _refuse_route(
    handler: BaseHTTPRequestHandler,
    request_id: int,
    code: str,
    message: str,
    *,
    detail: str = "",
) -> None:
    """Refuse a local client's route; its error code is also its failure class and log event."""
    telemetry.record_failure(code)
    _send_refusal(handler, 404, code, message)
    operational_log.log(
        f"req={request_id} event={code} {detail}"
        f"path={operational_log.safe_request_path(handler.path)}"
    )


def _refuse_non_local(handler: BaseHTTPRequestHandler, request_id: int) -> None:
    """Refuse a client that is not local, which any web page can make the proxy do at will.

    Every refusal is counted, but it is not a failure of the proxy or an upstream, so it leaves the
    last failure as it was, and one log line covers each interval with the number refused in it.
    """
    code = "local_client_required"
    _send_refusal(handler, 403, code, "data requests are available only to a local native client")
    if refused := _LOCAL_CLIENT_REFUSAL_LOG.admit():
        operational_log.log(
            f"req={request_id} event={code} refused={refused} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )


def _request_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Return the shared upstream header projection for supported routes."""
    headers = dict(downstream.end_to_end_fields(handler.headers))
    headers["Accept-Encoding"] = "identity"
    return headers


def _read_responses_body(handler: BaseHTTPRequestHandler) -> tuple[bytes | None, str]:
    """Read one unambiguous length-framed body before protocol or upstream admission."""
    if handler.headers.get_all("Transfer-Encoding", []):
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
    return admission.read_bounded_body(handler, length, _SETTINGS.request_body_timeout)


def _relay_catalog(
    handler: BaseHTTPRequestHandler,
    request_id: int,
    target: addressing.Target,
    headers: dict[str, str],
) -> None:
    """Relay one model catalog read exactly once without Responses policy."""
    deadline = time.monotonic() + _SETTINGS.catalog_timeout
    try:
        response = upstream_exchange.open_readonly(target.url, "GET", headers, deadline=deadline)
    except Exception as error:  # noqa: BLE001 upstream-outcome
        expired = isinstance(error, ExchangeDeadlineError) or time.monotonic() >= deadline
        code = "catalog_deadline_exceeded" if expired else "catalog_transport_error"
        telemetry.record_failure(code)
        downstream.send_catalog_failure(
            handler,
            504 if expired else 502,
            code,
            timeout=_SETTINGS.catalog_timeout,
        )
        operational_log.log(
            f"req={request_id} event={code} upstream={target.origin} "
            f"{operational_log.safe_exception_context(error)} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )
        return
    if response.status >= HTTPStatus.BAD_REQUEST:
        try:
            telemetry.record_failure(f"catalog_http_{response.status}")
            operational_log.log(
                f"req={request_id} event=catalog_http_terminal upstream={target.origin} "
                f"status={response.status} path={operational_log.safe_request_path(handler.path)}"
            )
        except BaseException:
            response.close()
            raise

    def log_body_failure(exception: str) -> None:
        operational_log.log(
            f"req={request_id} event=catalog_body_failed upstream={target.origin} "
            f"exception={exception} path={operational_log.safe_request_path(handler.path)}"
        )

    downstream.relay_readonly_body(
        handler,
        response,
        deadline=deadline,
        max_bytes=_SETTINGS.catalog_max_bytes,
        log_failure=log_body_failure,
    )


def relay(handler: BaseHTTPRequestHandler, method: str) -> None:
    """Relay one data request from a native local client to the upstream that its path names."""
    request_id = admission.next_request_id()
    if not admission.is_local_client(handler.client_address[0], handler.headers):
        _refuse_non_local(handler, request_id)
        return
    target = addressing.resolve(handler.path)
    if target is None:
        _refuse_route(
            handler,
            request_id,
            "upstream_route_invalid",
            "request path does not name an upstream Responses or model-catalog target",
        )
        return
    is_responses = method == "POST" and target.resource == "responses"
    is_catalog = method == "GET" and target.resource == "models"
    if not (is_responses or is_catalog):
        _refuse_route(
            handler,
            request_id,
            "upstream_route_method_not_allowed",
            "request method is not supported for the named upstream resource",
            detail=f"method={method} ",
        )
        return
    headers = _request_headers(handler)
    if is_catalog:
        _relay_catalog(handler, request_id, target, headers)
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
            f"req={request_id} event={code} upstream={target.origin} "
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
            f"upstream={target.origin} reason={reason} "
            f"path={operational_log.safe_request_path(handler.path)}"
        )
        return
    body = projection.body
    path = operational_log.safe_request_path(handler.path)
    if len(body) >= _LARGE_REQUEST_BYTES:
        operational_log.log(
            f"req={request_id} event=large_request upstream={target.origin} "
            f"bytes={len(body)} path={path}"
        )
    operational_log.log(
        f"req={request_id} event=request_sanitized upstream={target.origin} "
        f"method={method} {note} path={path}"
    )
    telemetry.record_counter("responses_received")
    exchange = upstream_exchange.Exchange(
        handler,
        method,
        request_id,
        body,
        target.url,
        headers,
        is_responses,
        body,
        target,
        input_dependencies=projection.input_dependencies,
    )
    if _cooldown_active(exchange):
        return
    exchange.log("responses_admitted", f"active={admission.admit_response()} ")
    try:
        with downstream.write_deadline(handler, exchange.deadline):
            response = upstream_exchange.open_upstream(exchange)
            if response is None:
                return
            content_type = header_value(response.headers, "Content-Type")
            if "text/event-stream" in content_type.lower():
                downstream.relay_sse(exchange, response)
            else:
                downstream.relay_responses_json(exchange, response)
    except downstream.DownstreamDeadlineError:
        handler.close_connection = True
        exchange.log("downstream_write_deadline")
    except downstream.CLIENT_CLOSED:
        downstream.client_closed(exchange)
    except Exception as error:
        exchange.log(
            "responses_handler_exception",
            f"exception={operational_log.safe_exception_label(error)} ",
        )
        raise
    finally:
        active_now = admission.release_response()
        exchange.log("responses_released", f"active={active_now} ")
