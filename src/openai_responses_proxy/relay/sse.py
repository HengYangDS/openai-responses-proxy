"""Validated Server-Sent Events relay for OpenAI Responses Proxy.

The module owns SSE framing, lazy downstream commitment, bounded pre-content
reconnects, and stream outcome accounting. JSON event mutation is delegated to
:mod:`openai_responses_proxy.protocol.response`; request admission and upstream
orchestration remain in :mod:`openai_responses_proxy.relay.responses`.
"""

from __future__ import annotations

import http.client
import time
from collections.abc import Callable
from contextlib import suppress
from http.server import BaseHTTPRequestHandler
from itertools import chain
from typing import NotRequired
from typing import Protocol
from typing import TypedDict
from typing import runtime_checkable

from openai_responses_proxy.protocol import agent_delivery
from openai_responses_proxy.protocol import response as live_response
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay.upstream import UpstreamResponse
from openai_responses_proxy.runtime import config as runtime_config

UPSTREAM_READ_TIMEOUT = runtime_config.load().upstream_read_timeout
UPSTREAM_TIMEOUT = runtime_config.load().upstream_timeout
_HELD_TYPES = frozenset(("response.created", "response.in_progress"))
_TERMINALS = frozenset(("response.completed", "response.failed", "response.incomplete"))
_CLEAN_TERMINALS = {"response.completed", "response.incomplete"}


@runtime_checkable
class _TimeoutSocket(Protocol):
    """Socket capability required to arm one bounded upstream read."""

    def settimeout(self, timeout: float) -> None:
        """Set the next blocking operation's deadline."""
        ...


class StreamResult(TypedDict):
    """Outcome of reading one upstream SSE response."""

    terminal: str | None
    events: int
    wrote_downstream: bool
    detail: str
    error: BaseException | None
    failure_code: NotRequired[str]
    upstream_request_id: NotRequired[str]


class RelayResult(TypedDict):
    """Outcome of the bounded pre-content reconnect policy."""

    pre_content_exhausted: bool
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


def _set_read_timeout(response: UpstreamResponse, timeout: float) -> None:
    current = response.fp
    for path in (("raw", "_sock"), ("raw", "_fp", "fp", "raw", "_sock")):
        candidate = current
        for attribute in path:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if isinstance(candidate, _TimeoutSocket):
            candidate.settimeout(timeout)
            return


def _release_upstream(response: UpstreamResponse) -> None:
    """Release one upstream connection the relay will never read from again.

    A stream abandoned at its deadline, or replaced by a pre-content reconnect,
    would otherwise hold its socket until garbage collection.
    """
    with suppress(Exception):
        response.close()


def _arm_read_budget(
    response: UpstreamResponse, deadline: float, armed: float | None
) -> float | None:
    """Clamp the per-read socket timeout to the remaining total-stream budget.

    Returns the armed budget, or ``None`` once the total deadline has passed.
    Re-arming as the deadline nears is what keeps a blocked read from
    outliving it by a whole idle-read interval.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    budget = min(UPSTREAM_READ_TIMEOUT, remaining)
    if armed is None or budget < armed:
        _set_read_timeout(response, budget)
    return budget


def _pop_event(buffer: bytes) -> tuple[bytes, bytes] | None:
    """Split the first complete SSE event from a byte buffer."""
    separators = tuple(
        (index, len(marker))
        for marker in (b"\n\n", b"\r\n\r\n")
        if (index := buffer.find(marker)) >= 0
    )
    if not separators:
        return None
    index, separator_length = min(separators)
    boundary = index + separator_length
    return buffer[:boundary], buffer[boundary:]


def _read_one_stream(
    handler: BaseHTTPRequestHandler,
    response: UpstreamResponse,
    on_first_write: Callable[[], None],
    deadline: float | None = None,
    plaintext_agent_delivery: bool = False,
) -> StreamResult:
    """Relay one upstream stream while withholding retry-safe prelude events."""
    if deadline is None:
        deadline = time.monotonic() + UPSTREAM_TIMEOUT
    buffer = b""
    event_count = 0
    terminal_event = None
    upstream_detail = "eof"
    upstream_error: BaseException | None = None
    failure_code, upstream_request_id = "unknown", "none"
    wrote_downstream = False
    prelude: list[bytes] = []

    def emit(data: bytes, *, hold: bool) -> None:
        nonlocal wrote_downstream
        if hold and not wrote_downstream:
            prelude.append(data)
            return
        if not wrote_downstream:
            on_first_write()
            wrote_downstream = True
        for event in chain(prelude, (data,)):
            handler.wfile.write(b"%X\r\n%s\r\n" % (len(event), event))
        prelude.clear()

    def process_event(event: bytes) -> None:
        nonlocal event_count, terminal_event, upstream_detail, upstream_error
        nonlocal failure_code, upstream_request_id
        try:
            event = agent_delivery.restore_event(event, plaintext_agent_delivery)
            payload = live_response.sse_event_data(event)
        except ValueError as error:
            upstream_detail = "projection_failed"
            upstream_error = error
            failure_code = live_response.validation_reason(error)
            return
        event_count += 1
        event_type = payload.get("type") if isinstance(payload, dict) else None
        event_type = event_type if isinstance(event_type, str) else ""
        terminal_event = event_type if event_type in _TERMINALS else terminal_event
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

    armed: float | None = None
    while True:
        armed = _arm_read_budget(response, deadline, armed)
        if armed is None:
            upstream_detail = "deadline"
            break
        try:
            chunk = response.read1(8192)
        except http.client.IncompleteRead as error:
            chunk = error.partial
            upstream_detail = "incomplete_read"
        except TimeoutError as error:
            upstream_detail, upstream_error = "timeout", error
            if time.monotonic() >= deadline:
                upstream_detail = "deadline"
            break
        except Exception as error:
            upstream_error = error
            break
        if not chunk:
            break
        buffer += chunk
        while split := _pop_event(buffer):
            event, buffer = split
            process_event(event)
            if terminal_event is not None or upstream_detail == "projection_failed":
                buffer = b""
                break
        if terminal_event is not None or upstream_detail == "projection_failed":
            break
    if buffer:
        process_event(buffer)
    detail = terminal_event.rpartition(".")[2] if terminal_event else upstream_detail
    return {
        "terminal": terminal_event,
        "events": event_count,
        "wrote_downstream": wrote_downstream,
        "detail": detail,
        "error": upstream_error,
        "failure_code": failure_code,
        "upstream_request_id": upstream_request_id,
    }


def relay(
    handler: BaseHTTPRequestHandler,
    response: UpstreamResponse,
    path: str,
    request_id: int,
    reopen: Callable[[], UpstreamResponse] | None = None,
    send_headers: Callable[[], None] | None = None,
    plaintext_agent_delivery: bool = False,
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
    deadline = time.monotonic() + UPSTREAM_TIMEOUT
    current = response
    result: StreamResult | None = None
    attempt = 0
    for attempt in range(max_attempts):
        try:
            result = _read_one_stream(
                handler, current, on_first_write, deadline, plaintext_agent_delivery
            )
        finally:
            _release_upstream(current)
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
        if (
            result["wrote_downstream"]
            or terminal in _CLEAN_TERMINALS
            or result["detail"] == "projection_failed"
        ):
            break
        delay = backoffs[min(attempt, len(backoffs) - 1)]
        if attempt == max_attempts - 1 or time.monotonic() + delay >= deadline:
            break
        telemetry.record_counter("streams_pre_content_reconnect_attempts")
        why = terminal or result["detail"]
        operational_log.log(
            f"req={request_id} event=sse_pre_content_reconnect reason={why} "
            f"events={result['events']} attempt={attempt + 1}/{max_attempts - 1} "
            f"path={operational_log.safe_request_path(path)}"
        )
        time.sleep(delay)
        assert reopen is not None
        try:
            current = reopen()
        except Exception as error:
            operational_log.log(
                f"req={request_id} event=sse_reconnect_failed "
                f"exception={operational_log.safe_exception_label(error)} "
                f"path={operational_log.safe_request_path(path)}"
            )
            break
    pre_content_exhausted = not headers_sent
    assert result is not None
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
        elif pre_content_exhausted:
            telemetry.record_counter("streams_pre_content_exhausted")
            telemetry.record_failure("stream_pre_content_exhausted")
        else:
            telemetry.record_failure(f"stream_{detail}")
    safe_path = operational_log.safe_request_path(path)
    if result["terminal"]:
        operational_log.log(
            f"req={request_id} event=sse_terminal terminal={result['terminal']} "
            f"events={result['events']} path={safe_path}"
        )
    else:
        detail = result["detail"]
        error = result["error"]
        operational_log.log(
            f"req={request_id} event=sse_end_without_terminal detail={detail} "
            f"exception={operational_log.safe_exception_label(error) if error else 'none'} "
            f"reason={result.get('failure_code', 'unknown')} "
            f"events={result['events']} path={safe_path}"
        )
    return {
        "pre_content_exhausted": pre_content_exhausted,
        "attempts": attempt + 1,
        "result": result,
    }
