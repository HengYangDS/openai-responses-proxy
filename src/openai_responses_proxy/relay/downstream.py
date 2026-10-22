"""Downstream HTTP framing and streaming relay."""

from __future__ import annotations

import http.client
import time
import urllib.error
from http.server import BaseHTTPRequestHandler
from typing import Protocol

from openai_responses_proxy.protocol import agent_delivery
from openai_responses_proxy.protocol import response as live_response
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay import upstream as upstream_transport
from openai_responses_proxy.relay.upstream import ResponseHeaders
from openai_responses_proxy.relay.upstream import UpstreamResponse

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}
MAX_RESPONSES_JSON_BYTES = 8 * 1024 * 1024


class Exchange(Protocol):
    """Exchange surface required by the downstream relay."""

    handler: BaseHTTPRequestHandler
    request_id: int
    is_responses: bool
    used_input_variant_dialogue: bool
    plaintext_agent_delivery: bool
    deadline: float

    def upstream(
        self, body: bytes | None = None, *, deadline: float | None = None
    ) -> UpstreamResponse:
        """Open the current attempt within an optional existing deadline."""

    def log(self, event: str, detail: str = "") -> None:
        """Record a bounded request event."""

    def input_variant_accepted(self) -> None:
        """Record accepted input-variant recovery."""

    def input_variant_exhausted(self, detail: str) -> None:
        """Record exhausted input-variant recovery."""


def send_payload(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: bytes,
    *,
    content_type: str = "application/json",
    retry_after: str | None = None,
    close: bool = False,
) -> None:
    """Send one length-delimited local response."""
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    if retry_after is not None:
        handler.send_header("Retry-After", retry_after)
    if close:
        handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def send_wire_failure_exhausted(
    handler: BaseHTTPRequestHandler,
    policy: provider_registry.WirePolicy,
    attempts: int,
) -> None:
    """Emit one provider-policy empty-response exhaustion."""
    payload = live_response.error_payload(
        policy.EXHAUSTED_MESSAGE,
        "upstream_unavailable",
        policy.EXHAUSTED_CODE,
        attempts=attempts,
    )
    send_payload(handler, 503, payload, retry_after="3")


def relay_error(
    handler: BaseHTTPRequestHandler,
    status: int,
    headers: ResponseHeaders,
    payload: bytes,
) -> None:
    """Relay one bounded upstream HTTP error."""
    handler.send_response(status)
    for name, value in headers.items():
        if name.lower() not in HOP_BY_HOP:
            handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _send_stream_headers(handler: BaseHTTPRequestHandler, response: UpstreamResponse) -> None:
    handler.send_response(response.status)
    for name, value in response.headers.items():
        if name.lower() not in HOP_BY_HOP and name.lower() != "content-length":
            handler.send_header(name, value)
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()


def relay_sse(exchange: Exchange, response: UpstreamResponse) -> None:
    """Relay one Responses SSE stream with bounded pre-content recovery."""

    def reopen(deadline: float) -> UpstreamResponse:
        return exchange.upstream(deadline=deadline)

    try:
        result = sse.relay(
            exchange.handler,
            response,
            exchange.handler.path,
            exchange.request_id,
            reopen=None if exchange.used_input_variant_dialogue else reopen,
            send_headers=lambda: _send_stream_headers(exchange.handler, response),
            plaintext_agent_delivery=exchange.plaintext_agent_delivery,
            deadline=exchange.deadline,
        )
        stream = result["result"]
        if (
            result["pre_content_exhausted"]
            and stream is not None
            and (stream["detail"] == "deadline" or time.monotonic() >= exchange.deadline)
        ):
            deadline_exceeded(exchange)
        elif stream is not None and stream["detail"] == "projection_failed":
            if result["pre_content_exhausted"]:
                send_payload(
                    exchange.handler,
                    503,
                    live_response.error_payload(
                        "Upstream stream could not be projected safely; retry the turn",
                        "upstream_unavailable",
                        "stream_projection_failed",
                    ),
                    retry_after="3",
                )
            exchange.log("sse_projection_failed")
        elif stream is not None and stream["detail"] == "buffer_limit":
            if result["pre_content_exhausted"]:
                send_payload(
                    exchange.handler,
                    503,
                    live_response.error_payload(
                        "Upstream stream exceeded its pending byte budget",
                        "upstream_unavailable",
                        "stream_buffer_limit",
                    ),
                )
            exchange.log("sse_buffer_limit")
        elif result["pre_content_exhausted"]:
            if exchange.used_input_variant_dialogue:
                exchange.input_variant_exhausted("")
            send_payload(
                exchange.handler,
                503,
                sse.exhausted_payload(result["attempts"]),
                retry_after="3",
            )
            exchange.log("sse_pre_content_exhausted", f"attempts={result['attempts']} ")
        else:
            exchange.input_variant_accepted()
    except (BrokenPipeError, ConnectionResetError):
        exchange.log("downstream_client_closed")
    except Exception as error:
        exchange.log(
            "stream_handler_exception",
            f"exception={operational_log.safe_exception_label(error)} ",
        )


def _read_chunk(response: UpstreamResponse, deadline: float, max_bytes: int) -> tuple[bytes, bool]:
    try:
        return upstream_transport.read_chunk(response, deadline, max_bytes), False
    except http.client.IncompleteRead as error:
        return error.partial, True


def _arm_write_timeout(
    handler: BaseHTTPRequestHandler, deadline: float, original_timeout: float | None
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise upstream_transport.ExchangeDeadlineError("catalog downstream deadline expired")
    handler.connection.settimeout(
        remaining if original_timeout is None else min(original_timeout, remaining)
    )


def send_catalog_failure(
    handler: BaseHTTPRequestHandler, status: int, code: str, *, timeout: float
) -> None:
    """Deliver one local failure with a finite independent error-write budget."""
    previous_timeout = handler.connection.gettimeout()
    handler.close_connection = True
    try:
        _arm_write_timeout(handler, time.monotonic() + timeout, previous_timeout)
        send_payload(
            handler,
            status,
            live_response.error_payload(
                "Upstream model catalog unavailable; retry discovery shortly",
                "upstream_unavailable",
                code,
            ),
            close=True,
        )
    except Exception:
        pass
    finally:
        handler.connection.settimeout(previous_timeout)


def relay_readonly_body(
    handler: BaseHTTPRequestHandler, response: UpstreamResponse, *, deadline: float, max_bytes: int
) -> None:
    """Relay catalog success or error once within its shared time and byte limits."""
    previous_timeout = handler.connection.gettimeout()
    native = response.fp if isinstance(response, urllib.error.HTTPError) else response
    remaining_bytes = max_bytes
    try:
        _arm_write_timeout(handler, deadline, previous_timeout)
        _send_stream_headers(handler, response)
        while True:
            chunk, terminal = _read_chunk(response, deadline, min(8192, remaining_bytes + 1))
            overflow = len(chunk) > remaining_bytes
            chunk = chunk[:remaining_bytes]
            if chunk:
                _arm_write_timeout(handler, deadline, previous_timeout)
                handler.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                remaining_bytes -= len(chunk)
            # Bounded stdlib reads can return EOF with Content-Length still unmet.
            if (
                overflow
                or terminal
                or (
                    not chunk
                    and isinstance(native, http.client.HTTPResponse)
                    and native.length is not None
                    and native.length != 0
                )
            ):
                handler.close_connection = True
                return
            if not chunk:
                break
        _arm_write_timeout(handler, deadline, previous_timeout)
        handler.wfile.write(b"0\r\n\r\n")
    except Exception:
        handler.close_connection = True
    finally:
        try:
            response.close()
        finally:
            handler.connection.settimeout(previous_timeout)


def _invalid_responses_success(exchange: Exchange, reason: str) -> None:
    telemetry.record_counter("invalid_responses_success_bodies")
    telemetry.record_failure("invalid_responses_success_body")
    send_payload(
        exchange.handler,
        503,
        live_response.error_payload(
            "Upstream returned an invalid successful Responses body; retry the turn",
            "upstream_unavailable",
            "invalid_responses_success_body",
            reason=reason,
        ),
        retry_after="3",
    )
    exchange.log("invalid_responses_success_body", f"reason={reason} ")


def deadline_exceeded(exchange: Exchange) -> None:
    """Report one expired exchange before any downstream commitment."""
    telemetry.record_failure("upstream_deadline_exceeded")
    send_payload(
        exchange.handler,
        504,
        live_response.error_payload(
            "Upstream exchange exceeded its total deadline; retry the turn",
            "upstream_unavailable",
            "upstream_deadline_exceeded",
        ),
    )
    exchange.log("upstream_deadline_exceeded")


def relay_responses_json(exchange: Exchange, response: UpstreamResponse) -> None:
    """Validate one complete non-stream Responses body before commitment."""
    try:
        body = upstream_transport.read_body(response, exchange.deadline, MAX_RESPONSES_JSON_BYTES)
        payload = live_response.validate_json_response(body)
        payload = agent_delivery.restore_response(payload, exchange.plaintext_agent_delivery)
    except upstream_transport.ExchangeDeadlineError:
        deadline_exceeded(exchange)
        return
    except upstream_transport.ResponseSizeError:
        _invalid_responses_success(exchange, "response_too_large")
        return
    except http.client.IncompleteRead:
        _invalid_responses_success(exchange, "incomplete_read")
        return
    except ValueError:
        _invalid_responses_success(exchange, "invalid_terminal_json")
        return
    except Exception as error:
        _invalid_responses_success(exchange, operational_log.safe_exception_label(error))
        return
    finally:
        response.close()

    send_payload(exchange.handler, response.status, payload)
    telemetry.record_counter("responses_completed")
    exchange.input_variant_accepted()
