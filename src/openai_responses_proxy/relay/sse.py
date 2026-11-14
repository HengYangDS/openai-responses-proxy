"""Validated Server-Sent Events relay for OpenAI Responses Proxy.

The module owns SSE framing, lazy downstream commitment, bounded pre-content
reconnects, and stream outcome accounting. JSON event mutation is delegated to
:mod:`openai_responses_proxy.protocol.response`; request admission and upstream
orchestration remain in :mod:`openai_responses_proxy.relay.responses`.
"""

from __future__ import annotations

import http.client
import time
from contextlib import suppress
from typing import TYPE_CHECKING
from typing import NotRequired
from typing import TypedDict

from openai_responses_proxy.protocol import agent_delivery
from openai_responses_proxy.protocol import response as live_response
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay import upstream as upstream_transport
from openai_responses_proxy.runtime import config as runtime_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from http.server import BaseHTTPRequestHandler

    from openai_responses_proxy.relay.upstream import UpstreamResponse

UPSTREAM_READ_TIMEOUT = runtime_config.load().upstream_read_timeout
UPSTREAM_TIMEOUT = runtime_config.load().upstream_timeout
SSE_BUFFER_MAX_BYTES = runtime_config.load().sse_buffer_max_bytes
_HELD_TYPES = frozenset(("response.created", "response.in_progress"))
# A top-level `error` event ends a Responses stream like the three terminal response events.
_TERMINALS = frozenset(("response.completed", "response.failed", "response.incomplete", "error"))


class StreamResult(TypedDict):
    """Outcome of reading one upstream SSE response."""

    terminal: str | None
    events: int
    wrote_downstream: bool
    detail: str
    error: BaseException | None
    failure_code: NotRequired[str]
    upstream_request_id: NotRequired[str]
    last_event_type: NotRequired[str]
    error_code: NotRequired[str]


class RelayResult(TypedDict):
    """Outcome of the bounded pre-content reconnect policy."""

    pre_content_exhausted: bool
    outcome_unknown: bool
    attempts: int
    result: StreamResult | None


def exhausted_payload(attempts: int) -> bytes:
    """Return a retryable local failure after pre-content SSE exhaustion."""
    return live_response.error_payload(
        "Upstream stream ended before content after bounded reconnects; retry the turn",
        "upstream_unavailable",
        "stream_pre_content_exhausted",
        attempts=attempts,
    )


def _release_upstream(response: UpstreamResponse) -> None:
    """Release one upstream connection the relay will never read from again.

    A stream abandoned at its deadline, or replaced by a pre-content reconnect,
    would otherwise hold its socket until garbage collection.
    """
    with suppress(Exception):
        response.close()


class StreamBufferLimitError(ValueError):
    """Pending SSE data exceeded the process's declared byte budget."""


def _require_buffer_budget(size: int) -> None:
    if size > SSE_BUFFER_MAX_BYTES:
        raise StreamBufferLimitError("Pending SSE data exceeds its byte budget")


def _pop_event(buffer: bytearray, start: int) -> bytes | None:
    """Remove the first complete event, searching only the newly extended suffix."""
    separators = tuple(
        (index, len(marker))
        for marker in (b"\n\n", b"\r\n\r\n")
        if (index := buffer.find(marker, start)) >= 0
    )
    if not separators:
        _require_buffer_budget(len(buffer))
        return None
    index, separator_length = min(separators)
    boundary = index + separator_length
    _require_buffer_budget(boundary)
    event = bytes(buffer[:boundary])
    del buffer[:boundary]
    return event


def _read_one_stream(
    handler: BaseHTTPRequestHandler,
    response: UpstreamResponse,
    on_first_write: Callable[[], None],
    deadline: float | None = None,
    *,
    plaintext_agent_delivery: bool = False,
) -> StreamResult:
    """Relay one upstream stream while withholding retry-safe prelude events."""
    if deadline is None:
        deadline = time.monotonic() + UPSTREAM_TIMEOUT
    buffer = bytearray()
    event_count = 0
    terminal_event = None
    upstream_detail = "eof"
    upstream_error: BaseException | None = None
    failure_code, upstream_request_id = "unknown", "none"
    last_event_type, error_code = "none", "unknown"
    wrote_downstream = False
    prelude = bytearray()
    prelude_bytes = 0

    def emit(data: bytes, *, hold: bool) -> None:
        nonlocal wrote_downstream, prelude_bytes
        if hold and not wrote_downstream:
            _require_buffer_budget(prelude_bytes + len(data))
            prelude.extend(b"%X\r\n%s\r\n" % (len(data), data))
            prelude_bytes += len(data)
            return
        if not wrote_downstream:
            on_first_write()
            wrote_downstream = True
        handler.wfile.write(prelude)
        handler.wfile.write(b"%X\r\n%s\r\n" % (len(data), data))
        prelude.clear()
        prelude_bytes = 0

    def process_event(event: bytes) -> None:
        nonlocal event_count, terminal_event, upstream_detail, upstream_error
        nonlocal failure_code, upstream_request_id, last_event_type, error_code
        try:
            event = agent_delivery.restore_event(event, active=plaintext_agent_delivery)
            payload = live_response.sse_event_data(event)
        except (TypeError, ValueError) as error:
            upstream_detail = "projection_failed"
            upstream_error = error
            failure_code = live_response.validation_reason(error)
            return
        event_count += 1
        event_type = payload.get("type") if isinstance(payload, dict) else None
        event_type = event_type if isinstance(event_type, str) else ""
        last_event_type = live_response.event_type_label(event_type)
        terminal_event = event_type if event_type in _TERMINALS else terminal_event
        if event_type == "error":
            error_code = live_response.stream_error_code(payload)
        response_body = payload.get("response") if isinstance(payload, dict) else None
        error = response_body.get("error") if isinstance(response_body, dict) else None
        if event_type == "response.failed":
            failure_code, upstream_request_id = live_response.failure_diagnostic(error)
        retryable = (
            event_type == "response.failed"
            and failure_code == "server_error"
            and isinstance(response_body, dict)
            and not response_body.get("output")
        )
        emit(event, hold=payload is None or event_type in _HELD_TYPES or retryable)

    while True:
        try:
            chunk = upstream_transport.read_chunk(
                response, deadline, 8192, idle_timeout=UPSTREAM_READ_TIMEOUT
            )
        except http.client.IncompleteRead as error:
            chunk = error.partial
            upstream_detail = "incomplete_read"
        except TimeoutError as error:
            upstream_detail, upstream_error = "timeout", error
            if (
                isinstance(error, upstream_transport.ExchangeDeadlineError)
                or time.monotonic() >= deadline
            ):
                upstream_detail = "deadline"
                break
            chunk = b""
        except Exception as error:  # noqa: BLE001 stream-outcome
            upstream_error = error
            chunk = b""
        search_start = max(0, len(buffer) - 3)
        buffer.extend(chunk)
        try:
            while (event := _pop_event(buffer, search_start)) is not None:
                process_event(event)
                search_start = 0
                if terminal_event is not None or upstream_detail == "projection_failed":
                    buffer.clear()
                    break
            if buffer and not chunk:
                process_event(bytes(buffer))
                buffer.clear()
        except StreamBufferLimitError as error:
            terminal_event = None
            upstream_detail, failure_code = "buffer_limit", "stream_buffer_limit"
            upstream_error = error
            buffer.clear()
            break
        if not chunk or terminal_event is not None or upstream_detail == "projection_failed":
            break
    detail = terminal_event.rpartition(".")[2] if terminal_event is not None else upstream_detail
    return {
        "terminal": terminal_event,
        "events": event_count,
        "wrote_downstream": wrote_downstream,
        "detail": detail,
        "error": upstream_error,
        "failure_code": failure_code,
        "upstream_request_id": upstream_request_id,
        "last_event_type": last_event_type,
        "error_code": error_code,
    }


def relay(
    handler: BaseHTTPRequestHandler,
    response: UpstreamResponse,
    path: str,
    request_id: int,
    reopen: Callable[[float], UpstreamResponse] | None = None,
    *,
    send_headers: Callable[[], None] | None = None,
    plaintext_agent_delivery: bool = False,
    deadline: float | None = None,
) -> RelayResult:
    """Relay validated SSE with retries only before downstream commitment."""
    headers_sent = False
    send_headers = send_headers or (lambda: None)

    def on_first_write() -> None:
        nonlocal headers_sent
        send_headers()
        headers_sent = True

    max_attempts = 6 if reopen is not None else 1
    backoffs = (1.0, 2.0, 4.0, 6.0, 8.0)
    if deadline is None:
        deadline = time.monotonic() + UPSTREAM_TIMEOUT

    def read(stream: UpstreamResponse) -> StreamResult:
        try:
            return _read_one_stream(
                handler,
                stream,
                on_first_write,
                deadline,
                plaintext_agent_delivery=plaintext_agent_delivery,
            )
        finally:
            _release_upstream(stream)

    result = read(response)
    attempts_started = 1
    reopened_outcome_unknown = False
    for attempt in range(max_attempts):
        terminal = result["terminal"]
        if terminal == "response.failed":
            code = result.get("failure_code", "unknown")
            telemetry.record_upstream_classification(f"sse_{code}")
            operational_log.log(
                f"req={request_id} event=sse_upstream_failed code={code} "
                f"upstream_request_id={result.get('upstream_request_id', 'none')} "
                f"committed={result['wrote_downstream']} attempt={attempt + 1} "
                f"path={operational_log.safe_request_path(path)}"
            )
        # Only an explicit pre-content failure is reopened. After any other ending the upstream
        # may have accepted the request, so it is not sent again.
        if (
            result["wrote_downstream"]
            or terminal != "response.failed"
            or result["detail"] in {"projection_failed", "deadline", "buffer_limit"}
        ):
            break
        delay = backoffs[min(attempt, len(backoffs) - 1)]
        if reopen is None or attempt == max_attempts - 1 or time.monotonic() + delay >= deadline:
            break
        telemetry.record_counter("streams_pre_content_reconnect_attempts")
        why = terminal or result["detail"]
        operational_log.log(
            f"req={request_id} event=sse_pre_content_reconnect reason={why} "
            f"events={result['events']} attempt={attempt + 1}/{max_attempts - 1} "
            f"path={operational_log.safe_request_path(path)}"
        )
        time.sleep(delay)
        if time.monotonic() >= deadline:
            break
        attempts_started += 1
        try:
            reopened = reopen(deadline)
        except Exception as error:  # noqa: BLE001 upstream-outcome
            if (
                isinstance(error, upstream_transport.ExchangeDeadlineError)
                or time.monotonic() >= deadline
            ):
                result["detail"] = "deadline"
            reopened_outcome_unknown = isinstance(
                error, upstream_transport.UpstreamOutcomeUnknownError
            )
            operational_log.log(
                f"req={request_id} event=sse_reconnect_failed "
                f"exception={operational_log.safe_exception_label(error)} "
                f"path={operational_log.safe_request_path(path)}"
            )
            break
        result = read(reopened)
    pre_content_exhausted = not headers_sent
    outcome_unknown = pre_content_exhausted and (
        reopened_outcome_unknown
        or (
            result["terminal"] is None
            and result["detail"] not in {"projection_failed", "deadline", "buffer_limit"}
        )
    )
    if headers_sent:
        if result["terminal"] is not None:
            handler.wfile.write(b"0\r\n\r\n")
        else:
            handler.close_connection = True
    if result["terminal"] == "response.completed":
        telemetry.record_counter("streams_completed")
        telemetry.record_counter("responses_completed")
    elif result["terminal"] == "response.incomplete":
        telemetry.record_counter("streams_incomplete")
        telemetry.record_failure("stream_response_incomplete")
    else:
        telemetry.record_counter("streams_failed")
        detail = result["detail"]
        if detail == "projection_failed":
            telemetry.record_counter("stream_projection_failures")
            telemetry.record_failure("stream_projection_failed")
        elif outcome_unknown:
            # The exchange owner reports the outcome-unknown failure once, for streams and JSON
            # alike.
            pass
        elif pre_content_exhausted:
            telemetry.record_counter("streams_pre_content_exhausted")
            telemetry.record_failure("stream_pre_content_exhausted")
        else:
            telemetry.record_failure(f"stream_{detail}")
    safe_path = operational_log.safe_request_path(path)
    if result["terminal"] is not None:
        code = (
            f"code={result.get('error_code', 'unknown')} " if result["terminal"] == "error" else ""
        )
        operational_log.log(
            f"req={request_id} event=sse_terminal terminal={result['terminal']} {code}"
            f"events={result['events']} path={safe_path}"
        )
    else:
        detail = result["detail"]
        error = result["error"]
        operational_log.log(
            f"req={request_id} event=sse_end_without_terminal detail={detail} "
            f"exception={operational_log.safe_exception_label(error) if error else 'none'} "
            f"reason={result.get('failure_code', 'unknown')} "
            f"last={result.get('last_event_type', 'none')} "
            f"events={result['events']} path={safe_path}"
        )
    return {
        "pre_content_exhausted": pre_content_exhausted,
        "outcome_unknown": outcome_unknown,
        "attempts": attempts_started,
        "result": result,
    }
