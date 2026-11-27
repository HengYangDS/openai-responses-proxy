"""The ways in which an admitted response ends: the rows that the relay's ending contract drives."""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.fixtures import EXACT_ERROR
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import TimedResponse
from tests.relay.fixtures import http_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

PROVIDERS = provider_registry.load()
PATH = "/ucloud/v1/responses"
CURRENT = b'{"input":"current"}'
_HISTORY = json.dumps(
    {
        "input": [
            {"type": "message", "role": "user", "content": "old" * 1000},
            {"type": "message", "role": "user", "content": "current"},
        ]
    }
).encode()
_REFUSED = b'{"error":{"type":"invalid_request_error","code":"invalid_api_key"}}'
_CAPACITY = (
    b'{"error":{"type":"invalid_request_error","code":"validation_error",'
    b'"param":"","message":"Input is too long"}}'
)
_RESPONSE_FAILED = b'{"error":{"type":"server_error","code":"response_failed"}}'
_COMPLETED_JSON = b'{"id":"resp_done","status":"completed","output":[]}'
_CREATED = b'data: {"type":"response.created"}\n\n'
_DELTA = b'data: {"type":"response.output_text.delta","delta":"kept"}\n\n'
_COMPLETED = b'data: {"type":"response.completed"}\n\n'
_FAILED = b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
_ERROR = b'data: {"type":"error","code":"validation_error"}\n\n'
_UNPROJECTABLE = b"data: {not json\n\n"
_INPUT_RECOVERY = ("input_variant_validation_error", "input_variant_dialogue_recovery")
# A row that does not read the failure that `status` reports last.
UNREAD = "unread"


def _rejected(status: int, body: bytes) -> urllib.error.HTTPError:
    return http_error(status, "Rejected", body)


def _unreadable_rejection() -> urllib.error.HTTPError:
    body = TimedResponse([0.0], [ConnectionResetError("private")], "application/json")
    return urllib.error.HTTPError(
        "https://upstream.invalid/v1/responses", 400, "Rejected", body.headers, body
    )


def _stream(*reads: bytes | BaseException) -> DirectResponse:
    return DirectResponse(*reads, content_type="text/event-stream")


def _one_attempt(mocker: MockerFixture) -> None:
    """Leave the exchange one attempt, which a recovery then spends."""
    mocker.patch.object(upstream_exchange, "_MAX_ATTEMPTS", 1)
    mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 0)
    mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_DIALOGUE_SLOTS", 0)
    mocker.patch.object(upstream_exchange, "INPUT_VARIANT_DIALOGUE_SLOTS", 0)


def _small_stream_buffer(mocker: MockerFixture) -> None:
    mocker.patch.object(sse, "SSE_BUFFER_MAX_BYTES", 16)


def _one_event_stream_buffer(mocker: MockerFixture) -> None:
    mocker.patch.object(sse, "SSE_BUFFER_MAX_BYTES", len(_DELTA))


@dataclass(frozen=True)
class Ending:
    """One way in which an admitted response ends.

    ``upstream`` returns what each attempt meets: a response, or the failure it raises.
    ``named`` is the line that names the ending, the last one before the release, and ``holds``
    a field of that line; ``steps`` are the lines before it: retries, recoveries and stream
    readings.
    ``client`` is the status and the error code that the client receives, or nothing complete.
    ``write`` is what the client's connection raises on a write. ``closed`` says that the
    handler then ends the connection, and ``leaves`` names an error that leaves the handler.
    ``failure`` is the class of the failure that `status` reports last, or none.
    """

    upstream: Callable[[], list[object]]
    named: str
    client: tuple[int, str | None] | None = None
    steps: tuple[str, ...] = ()
    holds: str = ""
    failure: str | None = UNREAD
    body: bytes = CURRENT
    path: str = PATH
    arrange: Callable[[MockerFixture], None] | None = None
    write: type[OSError] | None = None
    closed: bool = False
    leaves: type[OSError] | None = None


_BEFORE_A_RESPONSE = {
    "an upstream error": Ending(
        lambda: [_rejected(401, _REFUSED)],
        "upstream_http_terminal",
        (401, "invalid_api_key"),
    ),
    "an upstream error after its retries": Ending(
        lambda: [_rejected(500, _REFUSED) for _ in range(4)],
        "upstream_http_terminal",
        (500, "invalid_api_key"),
        steps=("upstream_retry", "upstream_retry", "upstream_retry"),
    ),
    "an upstream error with an unreadable body": Ending(
        lambda: [_unreadable_rejection()],
        "upstream_error_body_unreadable",
        (502, "upstream_error_body_unreadable"),
    ),
    "a capacity recovery that the upstream rejects": Ending(
        lambda: [_rejected(400, _CAPACITY), _rejected(400, _CAPACITY)],
        "input_capacity_recovery_rejected",
        (400, "validation_error"),
        steps=("input_capacity_recovery",),
        body=_HISTORY,
    ),
    "a capacity recovery that has nothing to drop": Ending(
        lambda: [_rejected(400, _CAPACITY)],
        "input_capacity_recovery_declined",
        (400, "validation_error"),
    ),
    "a capacity recovery that the transport fails": Ending(
        lambda: [_rejected(400, _CAPACITY), urllib.error.URLError("private")],
        "input_capacity_recovery_transport_error",
        (502, "input_capacity_recovery_transport_error"),
        steps=("input_capacity_recovery",),
        body=_HISTORY,
    ),
    "an input recovery that the upstream rejects": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _rejected(400, EXACT_ERROR)],
        "input_variant_dialogue_recovery_exhausted",
        (400, "validation_error"),
        steps=_INPUT_RECOVERY,
        body=_HISTORY,
    ),
    "an input recovery that the transport fails": Ending(
        lambda: [_rejected(400, EXACT_ERROR), OSError("private")],
        "input_variant_dialogue_recovery_exhausted",
        (502, "input_variant_recovery_transport_error"),
        steps=_INPUT_RECOVERY,
        body=_HISTORY,
    ),
    "an input recovery whose outcome is unknown": Ending(
        lambda: [
            _rejected(400, EXACT_ERROR),
            upstream_transport.UpstreamOutcomeUnknownError("private"),
        ],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_exhausted"),
        body=_HISTORY,
    ),
    "an input validation error that no recovery can shorten": Ending(
        lambda: [_rejected(400, EXACT_ERROR)],
        "upstream_http_terminal",
        (400, "validation_error"),
        steps=("input_variant_validation_error",),
    ),
    "a failed response that no recovery repairs": Ending(
        lambda: [_rejected(400, _RESPONSE_FAILED)],
        "response_failed_recovery_exhausted",
        (503, "response_failed_recovery_exhausted"),
    ),
    "a transport that fails every attempt": Ending(
        lambda: [OSError("private") for _ in range(4)],
        "upstream_transport_exhausted",
        (502, "upstream_transport_error"),
        steps=("upstream_transport_retry", "upstream_transport_retry", "upstream_transport_retry"),
        holds="exception=OSError ",
        failure="upstream_transport_error",
    ),
    "a recovery that spends the last attempt": Ending(
        lambda: [_rejected(400, _CAPACITY)],
        "upstream_transport_exhausted",
        (502, "upstream_transport_error"),
        steps=("input_capacity_recovery",),
        holds="exception=none ",
        failure="upstream_transport_error",
        body=_HISTORY,
        arrange=_one_attempt,
    ),
    "an unknown outcome before a response": Ending(
        lambda: [upstream_transport.UpstreamOutcomeUnknownError("private")],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
    ),
    "a deadline before a response": Ending(
        lambda: [upstream_transport.ExchangeDeadlineError("private")],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
    ),
}
_NOT_A_STREAM = {
    "a completed response": Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "json_terminal",
        (200, None),
    ),
    "a completed response after an accepted capacity recovery": Ending(
        lambda: [_rejected(400, _CAPACITY), DirectResponse(_COMPLETED_JSON)],
        "json_terminal",
        (200, None),
        steps=("input_capacity_recovery", "input_capacity_recovery_accepted"),
        body=_HISTORY,
    ),
    "a completed response after an accepted input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), DirectResponse(_COMPLETED_JSON)],
        "json_terminal",
        (200, None),
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_accepted"),
        body=_HISTORY,
    ),
    "a deadline inside a body": Ending(
        lambda: [DirectResponse(upstream_transport.ExchangeDeadlineError("private"))],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
    ),
    "an invalid body": Ending(
        lambda: [DirectResponse(b"not-json")],
        "invalid_responses_success_body",
        (503, "invalid_responses_success_body"),
    ),
    "a completed response whose client has closed": Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "downstream_client_closed",
        write=ConnectionResetError,
        closed=True,
    ),
    "an upstream error whose client has closed": Ending(
        lambda: [_rejected(401, _REFUSED)],
        "downstream_client_closed",
        write=BrokenPipeError,
        closed=True,
    ),
    "a local failure whose client has aborted": Ending(
        lambda: [upstream_transport.ExchangeDeadlineError("private")],
        "downstream_client_closed",
        write=ConnectionAbortedError,
        closed=True,
    ),
    "a rejected capacity recovery whose client has closed": Ending(
        lambda: [_rejected(400, _CAPACITY), _rejected(400, _CAPACITY)],
        "downstream_client_closed",
        steps=("input_capacity_recovery", "input_capacity_recovery_rejected"),
        body=_HISTORY,
        write=BrokenPipeError,
        closed=True,
    ),
    "a declined capacity recovery whose client has closed": Ending(
        lambda: [_rejected(400, _CAPACITY)],
        "downstream_client_closed",
        steps=("input_capacity_recovery_declined",),
        write=BrokenPipeError,
        closed=True,
    ),
    "a rejected input recovery whose client has closed": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _rejected(400, EXACT_ERROR)],
        "downstream_client_closed",
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_exhausted"),
        body=_HISTORY,
        write=BrokenPipeError,
        closed=True,
    ),
    "an input recovery that the transport fails and whose client has closed": Ending(
        lambda: [_rejected(400, EXACT_ERROR), OSError("private")],
        "downstream_client_closed",
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_exhausted"),
        body=_HISTORY,
        write=BrokenPipeError,
        closed=True,
    ),
    "an upstream error whose client stops reading": Ending(
        lambda: [_rejected(401, _REFUSED)],
        "downstream_write_deadline",
        write=TimeoutError,
        closed=True,
        failure=None,
    ),
    "a local failure whose client stops reading": Ending(
        lambda: [OSError("private") for _ in range(4)],
        "downstream_write_deadline",
        steps=("upstream_transport_retry", "upstream_transport_retry", "upstream_transport_retry"),
        write=TimeoutError,
        closed=True,
        failure="upstream_transport_error",
    ),
    "a completed response whose client stops reading": Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "downstream_write_deadline",
        write=TimeoutError,
        closed=True,
    ),
    "a completed response whose write fails otherwise": Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "responses_handler_exception",
        write=OSError,
        leaves=OSError,
    ),
}
_A_STREAM = {
    "a stream that completes": Ending(
        lambda: [_stream(_CREATED + _DELTA + _COMPLETED)],
        "sse_terminal",
        (200, None),
    ),
    "a stream that ends with an error event": Ending(
        lambda: [_stream(_CREATED + _ERROR)],
        "sse_terminal",
        (200, None),
    ),
    "a stream that fails after content": Ending(
        lambda: [_stream(_DELTA + _FAILED)],
        "sse_terminal",
        (200, None),
        steps=("sse_upstream_failed",),
    ),
    "a stream that stops after content": Ending(
        lambda: [_stream(_DELTA)],
        "sse_end_without_terminal",
        (200, None),
    ),
    "a stream that stops before content": Ending(
        lambda: [_stream()],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
        steps=("sse_end_without_terminal",),
    ),
    "a stream that completes after an accepted input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _stream(_CREATED + _DELTA + _COMPLETED)],
        "sse_terminal",
        (200, None),
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_accepted"),
        body=_HISTORY,
    ),
    "a stream that stops after content, after an accepted input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _stream(_DELTA)],
        "sse_end_without_terminal",
        (200, None),
        steps=(*_INPUT_RECOVERY, "input_variant_dialogue_recovery_accepted"),
        body=_HISTORY,
    ),
    "a stream that stops before content, after an input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _stream()],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
        steps=(
            *_INPUT_RECOVERY,
            "sse_end_without_terminal",
            "input_variant_dialogue_recovery_exhausted",
        ),
        body=_HISTORY,
    ),
    "a stream event that cannot be projected, after an input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _stream(_DELTA + _UNPROJECTABLE)],
        "sse_projection_failed",
        (200, None),
        steps=(*_INPUT_RECOVERY, "sse_end_without_terminal"),
        body=_HISTORY,
    ),
    "a stream over its buffer budget, after an input recovery": Ending(
        lambda: [_rejected(400, EXACT_ERROR), _stream(_DELTA + b"data: " + b"x" * 128)],
        "sse_buffer_limit",
        (200, None),
        steps=(*_INPUT_RECOVERY, "sse_end_without_terminal"),
        body=_HISTORY,
        arrange=_one_event_stream_buffer,
    ),
    "a stream deadline after content": Ending(
        lambda: [_stream(_DELTA, upstream_transport.ExchangeDeadlineError("private"))],
        "sse_end_without_terminal",
        (200, None),
    ),
    "a stream deadline before content": Ending(
        lambda: [_stream(upstream_transport.ExchangeDeadlineError("private"))],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
        steps=("sse_end_without_terminal",),
    ),
    "a stream event that cannot be projected after content": Ending(
        lambda: [_stream(_DELTA + _UNPROJECTABLE)],
        "sse_projection_failed",
        (200, None),
        steps=("sse_end_without_terminal",),
    ),
    "a stream event that cannot be projected before content": Ending(
        lambda: [_stream(_UNPROJECTABLE)],
        "sse_projection_failed",
        (503, "stream_projection_failed"),
        steps=("sse_end_without_terminal",),
    ),
    "a stream over its buffer budget": Ending(
        lambda: [_stream(b"data: " + b"x" * 64)],
        "sse_buffer_limit",
        (503, "stream_buffer_limit"),
        steps=("sse_end_without_terminal",),
        arrange=_small_stream_buffer,
    ),
    "a stream failure before content that no reconnect repairs": Ending(
        lambda: [_stream(_CREATED + _FAILED), OSError("private")],
        "sse_pre_content_exhausted",
        (503, "stream_pre_content_exhausted"),
        steps=(
            "sse_upstream_failed",
            "sse_pre_content_reconnect",
            "sse_reconnect_failed",
            "sse_terminal",
        ),
    ),
    "a stream whose client closes": Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_client_closed",
        write=BrokenPipeError,
        closed=True,
    ),
    "a stream whose client aborts": Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_client_closed",
        write=ConnectionAbortedError,
        closed=True,
    ),
    "a stream whose client stops reading": Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_write_deadline",
        write=TimeoutError,
        closed=True,
    ),
    "a stream whose write fails otherwise": Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "stream_handler_exception",
        write=OSError,
        closed=True,
    ),
}
ENDINGS = _BEFORE_A_RESPONSE | _NOT_A_STREAM | _A_STREAM
