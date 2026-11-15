"""Every ending of an admitted response has a line between its admission and its release."""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.protocol import response as live_response
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.empty_response.fixtures import EMPTY_RESPONSE
from tests.relay.fixtures import EXACT_ERROR
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import TimedResponse
from tests.relay.fixtures import http_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

_PROVIDERS = provider_registry.load()
_PATH = "/ucloud/v1/responses"
_CURRENT = b'{"input":"current"}'
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


@dataclass(frozen=True)
class _Ending:
    """One way in which an admitted response ends.

    ``upstream`` returns what each attempt meets: a response, or the failure it raises.
    ``named`` is the line that names the ending, the last one before the release; ``after`` are
    the lines of the retries, recoveries and stream readings that precede it.
    ``client`` is the status and the error code that the client receives, or nothing complete.
    ``write`` is what the client's connection raises on a write. ``closed`` says that the
    handler then ends the connection, and ``leaves`` names an error that leaves the handler.
    """

    upstream: Callable[[], list[object]]
    named: str
    client: tuple[int, str | None] | None = None
    after: tuple[str, ...] = ()
    body: bytes = _CURRENT
    path: str = _PATH
    arrange: Callable[[MockerFixture], None] | None = None
    write: type[OSError] | None = None
    closed: bool = False
    leaves: type[OSError] | None = None


_BEFORE_A_RESPONSE = {
    "an upstream error": _Ending(
        lambda: [_rejected(401, _REFUSED)],
        "upstream_http_terminal",
        (401, "invalid_api_key"),
    ),
    "an upstream error after its retries": _Ending(
        lambda: [_rejected(500, _REFUSED) for _ in range(4)],
        "upstream_http_terminal",
        (500, "invalid_api_key"),
        after=("upstream_retry", "upstream_retry", "upstream_retry"),
    ),
    "an upstream error with an unreadable body": _Ending(
        lambda: [_unreadable_rejection()],
        "upstream_error_body_unreadable",
        (502, "upstream_error_body_unreadable"),
    ),
    "a capacity recovery that the upstream rejects": _Ending(
        lambda: [_rejected(400, _CAPACITY), _rejected(400, _CAPACITY)],
        "input_capacity_recovery_rejected",
        (400, "validation_error"),
        after=("input_capacity_recovery",),
        body=_HISTORY,
    ),
    "a capacity recovery that has nothing to drop": _Ending(
        lambda: [_rejected(400, _CAPACITY)],
        "input_capacity_recovery_declined",
        (400, "validation_error"),
    ),
    "a capacity recovery that the transport fails": _Ending(
        lambda: [_rejected(400, _CAPACITY), urllib.error.URLError("private")],
        "input_capacity_recovery_transport_error",
        (502, "input_capacity_recovery_transport_error"),
        after=("input_capacity_recovery",),
        body=_HISTORY,
    ),
    "an input recovery that the upstream rejects": _Ending(
        lambda: [_rejected(400, EXACT_ERROR), _rejected(400, EXACT_ERROR)],
        "input_variant_dialogue_recovery_exhausted",
        (400, "validation_error"),
        after=("input_variant_validation_error", "input_variant_dialogue_recovery"),
        body=_HISTORY,
    ),
    "an input recovery that the transport fails": _Ending(
        lambda: [_rejected(400, EXACT_ERROR), OSError("private")],
        "input_variant_dialogue_recovery_exhausted",
        (502, "input_variant_recovery_transport_error"),
        after=("input_variant_validation_error", "input_variant_dialogue_recovery"),
        body=_HISTORY,
    ),
    "a failed response that no recovery repairs": _Ending(
        lambda: [_rejected(400, _RESPONSE_FAILED)],
        "response_failed_recovery_exhausted",
        (503, "response_failed_recovery_exhausted"),
    ),
    "a wire-policy failure that its retry repeats": _Ending(
        lambda: [_rejected(477, EMPTY_RESPONSE), _rejected(477, EMPTY_RESPONSE)],
        "wire_failure_retry_failed",
        (503, "dmx_empty_response_exhausted"),
        after=("wire_failure_retry",),
        path="/dmxapi/v1/responses",
    ),
    "a transport that fails every attempt": _Ending(
        lambda: [OSError("private") for _ in range(4)],
        "upstream_transport_exhausted",
        (502, "upstream_transport_error"),
        after=("upstream_transport_retry", "upstream_transport_retry", "upstream_transport_retry"),
    ),
    "a recovery that spends the last attempt": _Ending(
        lambda: [_rejected(400, _CAPACITY)],
        "upstream_transport_exhausted",
        (502, "upstream_transport_error"),
        after=("input_capacity_recovery",),
        body=_HISTORY,
        arrange=_one_attempt,
    ),
    "an unknown outcome before a response": _Ending(
        lambda: [upstream_transport.UpstreamOutcomeUnknownError("private")],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
    ),
    "a deadline before a response": _Ending(
        lambda: [upstream_transport.ExchangeDeadlineError("private")],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
    ),
}
_NOT_A_STREAM = {
    "a completed response": _Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "json_terminal",
        (200, None),
    ),
    "a deadline inside a body": _Ending(
        lambda: [DirectResponse(upstream_transport.ExchangeDeadlineError("private"))],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
    ),
    "an invalid body": _Ending(
        lambda: [DirectResponse(b"not-json")],
        "invalid_responses_success_body",
        (503, "invalid_responses_success_body"),
    ),
    "a completed response whose client has closed": _Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "downstream_client_closed",
        write=ConnectionResetError,
        closed=True,
    ),
    "an upstream error whose client has closed": _Ending(
        lambda: [_rejected(401, _REFUSED)],
        "downstream_client_closed",
        write=BrokenPipeError,
        closed=True,
    ),
    "a local failure whose client has aborted": _Ending(
        lambda: [upstream_transport.ExchangeDeadlineError("private")],
        "downstream_client_closed",
        write=ConnectionAbortedError,
        closed=True,
    ),
    "a completed response whose client stops reading": _Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "downstream_write_deadline",
        write=TimeoutError,
        closed=True,
    ),
    "a completed response whose write fails otherwise": _Ending(
        lambda: [DirectResponse(_COMPLETED_JSON)],
        "responses_handler_exception",
        write=OSError,
        leaves=OSError,
    ),
}
_A_STREAM = {
    "a stream that completes": _Ending(
        lambda: [_stream(_CREATED + _DELTA + _COMPLETED)],
        "sse_terminal",
        (200, None),
    ),
    "a stream that ends with an error event": _Ending(
        lambda: [_stream(_CREATED + _ERROR)],
        "sse_terminal",
        (200, None),
    ),
    "a stream that fails after content": _Ending(
        lambda: [_stream(_DELTA + _FAILED)],
        "sse_terminal",
        (200, None),
        after=("sse_upstream_failed",),
    ),
    "a stream that stops after content": _Ending(
        lambda: [_stream(_DELTA)],
        "sse_end_without_terminal",
        (200, None),
    ),
    "a stream that stops before content": _Ending(
        lambda: [_stream()],
        "upstream_outcome_unknown",
        (502, "upstream_outcome_unknown"),
        after=("sse_end_without_terminal",),
    ),
    "a stream deadline after content": _Ending(
        lambda: [_stream(_DELTA, upstream_transport.ExchangeDeadlineError("private"))],
        "sse_end_without_terminal",
        (200, None),
    ),
    "a stream deadline before content": _Ending(
        lambda: [_stream(upstream_transport.ExchangeDeadlineError("private"))],
        "upstream_deadline_exceeded",
        (504, "upstream_deadline_exceeded"),
        after=("sse_end_without_terminal",),
    ),
    "a stream event that cannot be projected after content": _Ending(
        lambda: [_stream(_DELTA + _UNPROJECTABLE)],
        "sse_projection_failed",
        (200, None),
        after=("sse_end_without_terminal",),
    ),
    "a stream event that cannot be projected before content": _Ending(
        lambda: [_stream(_UNPROJECTABLE)],
        "sse_projection_failed",
        (503, "stream_projection_failed"),
        after=("sse_end_without_terminal",),
    ),
    "a stream over its buffer budget": _Ending(
        lambda: [_stream(b"data: " + b"x" * 64)],
        "sse_buffer_limit",
        (503, "stream_buffer_limit"),
        after=("sse_end_without_terminal",),
        arrange=_small_stream_buffer,
    ),
    "a stream failure before content that no reconnect repairs": _Ending(
        lambda: [_stream(_CREATED + _FAILED), OSError("private")],
        "sse_pre_content_exhausted",
        (503, "stream_pre_content_exhausted"),
        after=(
            "sse_upstream_failed",
            "sse_pre_content_reconnect",
            "sse_reconnect_failed",
            "sse_terminal",
        ),
    ),
    "a stream whose client closes": _Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_client_closed",
        write=BrokenPipeError,
        closed=True,
    ),
    "a stream whose client aborts": _Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_client_closed",
        write=ConnectionAbortedError,
        closed=True,
    ),
    "a stream whose client stops reading": _Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "downstream_write_deadline",
        write=TimeoutError,
        closed=True,
    ),
    "a stream whose write fails otherwise": _Ending(
        lambda: [_stream(_DELTA + _COMPLETED)],
        "stream_handler_exception",
        write=OSError,
    ),
}
_ENDINGS = _BEFORE_A_RESPONSE | _NOT_A_STREAM | _A_STREAM


def _lines() -> list[str]:
    return Path(operational_log.LOG_PATH).read_text(encoding="utf-8").splitlines()


def _fields(line: str) -> dict[str, str]:
    """Return the fields of one line; a bare word, which redaction can leave, is no field."""
    return dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)


class TestAdmittedResponseEndings(InputTransportFixture):
    """The log of one admitted response holds its ending before its release."""

    @pytest.mark.parametrize("ending", _ENDINGS.values(), ids=_ENDINGS)
    def test_a_line_names_the_ending_between_admission_and_release(
        self, ending: _Ending, *, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        if ending.arrange is not None:
            ending.arrange(mocker)
        attempts = ending.upstream()
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=attempts)
        handler = MemoryHandler(ending.body, path=ending.path)
        writer = None
        if ending.write is not None:
            writer = mocker.Mock(**{"write.side_effect": ending.write("private")})
            handler.wfile = writer

        if ending.leaves is None:
            responses.relay(handler, "POST", _PROVIDERS)
        else:
            with pytest.raises(ending.leaves):
                responses.relay(handler, "POST", _PROVIDERS)

        events = [_fields(line)["event"] for line in _lines()]
        admitted = events.index("responses_admitted")
        assert events[-1] == "responses_released"
        assert events[admitted + 1 : -1] == [*ending.after, ending.named]
        assert opened.call_count == len(attempts)
        if writer is not None:
            # Nothing is written to a connection after a write to it has failed.
            assert writer.write.call_count == 1
        if ending.closed:
            assert getattr(handler, "close_connection", False)
        if ending.client is not None:
            status, code = ending.client
            assert handler.statuses == [status]
            if code is not None:
                assert json.loads(handler.output())["error"]["code"] == code
        assert self._status_snapshot()["active_responses"] == 0

    def _json_terminal(self, body: bytes, mocker: MockerFixture) -> dict[str, str]:
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=DirectResponse(body))
        handler = MemoryHandler(_CURRENT, path=_PATH)

        responses.relay(handler, "POST", _PROVIDERS)

        assert handler.statuses == [200]
        assert handler.output() == body
        [fields] = [
            fields for line in _lines() if (fields := _fields(line))["event"] == "json_terminal"
        ]
        return fields

    @pytest.mark.parametrize("status", ["completed", "incomplete"])
    def test_a_completed_response_that_is_not_a_stream_names_its_status(
        self, status: str, *, mocker: MockerFixture
    ) -> None:
        body = json.dumps({"id": "resp_done", "status": status, "output": []}).encode()

        fields = self._json_terminal(body, mocker)

        assert fields == {
            "req": "1",
            "event": "json_terminal",
            "provider": "ucloud",
            "path": _PATH,
            "status": status,
        }

    @pytest.mark.parametrize(
        ("status", "logged"),
        [
            ("Completed Now", "unknown"),
            ("completed-late", "unknown"),
            ("", "unknown"),
            ("a" * 65, "unknown"),
            ("private prose: leaked", "unknown"),
            ("a" + "b_9" * 21, "a" + "b_9" * 21),
            ("authorization", "authorization"),
            ("bearer", "bearer"),
            ("api_key", "api_key"),
            ("authorization_error", "[redacted]"),
            ("api_key_invalid", "[redacted]"),
            ("bearer_token_expired", "[redacted]"),
            ("invalid_api_key", "invalid_api_key"),
        ],
    )
    def test_a_status_is_logged_as_a_token_and_takes_no_other_field_with_it(
        self, status: str, logged: str, *, mocker: MockerFixture
    ) -> None:
        # The validator admits two statuses today, and neither is such a word. The line does not
        # rest on that: it holds a token or `unknown`, and the log's redaction of a word that
        # reads as a credential field, which also takes what follows it, finds nothing after it.
        mocker.patch.object(
            live_response, "validate_json_response", return_value=(_COMPLETED_JSON, status)
        )

        fields = self._json_terminal(_COMPLETED_JSON, mocker)

        assert fields["status"] == logged
        assert fields["path"] == _PATH
        assert fields["provider"] == "ucloud"
        assert "leaked" not in "".join(_lines())
