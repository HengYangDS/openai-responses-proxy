"""Contracts for a stream that ends with a top-level error event, or without a terminal one."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import request
from tests.relay.fixtures import request_body
from tests.relay.fixtures import running_proxy

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

_CREATED = b'data: {"type":"response.created"}\n\n'
_IN_PROGRESS = b'data: {"type":"response.in_progress"}\n\n'
_DELTA = b'data: {"type":"response.output_text.delta","delta":"kept"}\n\n'
_FINAL_CHUNK = b"0\r\n\r\n"


def _error_event(code: object = "validation_error") -> bytes:
    payload = {
        "type": "error",
        "code": code,
        "message": "private-upstream-prose for requestId 1234: web search is not supported",
        "param": "",
    }
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def _chunked(*events: bytes) -> bytes:
    return b"".join(b"%X\r\n%s\r\n" % (len(event), event) for event in events)


def _log_fields(path: Path, event: str) -> dict[str, str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = dict(field.split("=", 1) for field in line.split()[1:])
        if fields.get("event") == event:
            return fields
    raise AssertionError(f"no {event} line in the log")


@pytest.fixture
def log_path(tmp_path: Path, mocker: MockerFixture) -> Path:
    path = tmp_path / "proxy.log"
    mocker.patch.object(operational_log, "LOG_PATH", str(path))
    mocker.patch.object(operational_log.sys.stderr, "write")
    return path


class TestStreamErrorEvent:
    """A top-level error event ends the stream; an early ending names where it stopped."""

    def setup_method(self) -> None:
        telemetry.reset_for_test()

    @pytest.mark.parametrize(
        "before",
        [(), (_CREATED, _IN_PROGRESS), (_CREATED, _DELTA)],
        ids=["first event", "after held events", "after committed content"],
    )
    def test_an_error_event_ends_the_stream_once_and_completely(
        self, before: tuple[bytes, ...], log_path: Path, *, mocker: MockerFixture
    ) -> None:
        error = _error_event()
        # An upstream that sends more after its error event, or keeps its connection open,
        # is not read further: a second read would raise.
        upstream = DirectResponse(b"".join(before) + error, AssertionError("read after the end"))
        handler = MemoryHandler()
        reopen = mocker.Mock()

        result = sse.relay(handler, upstream, "/dmxapi/v1/responses", 7, reopen=reopen)

        assert handler.output() == _chunked(*before, error) + _FINAL_CHUNK
        assert not getattr(handler, "close_connection", False)
        reopen.assert_not_called()
        assert result["attempts"] == 1
        assert not result["outcome_unknown"]
        assert not result["pre_content_exhausted"]
        assert result["result"]["terminal"] == "error"
        fields = _log_fields(log_path, "sse_terminal")
        assert fields["terminal"] == "error"
        assert fields["code"] == "validation_error"
        assert fields["events"] == str(len(before) + 1)
        text = log_path.read_text(encoding="utf-8")
        assert "sse_end_without_terminal" not in text
        assert "private-upstream-prose" not in text
        assert "web" not in text
        status = telemetry.status(
            release="test",
            serving_payload_sha256=None,
            release_receipt_sha256=None,
            admission={},
            runtime_identity={},
        )
        counters, failure = status["counters"], status["last_failure"]
        assert isinstance(counters, dict)
        assert isinstance(failure, dict)
        assert counters["streams_failed"] == 1
        assert counters["streams_completed"] == 0
        assert failure["classification"] == "stream_error"

    @pytest.mark.parametrize(
        "code",
        [None, 7, "", "Validation Error", "validation-error", "a" * 65, "private prose: leaked"],
    )
    def test_a_code_outside_the_token_grammar_is_logged_as_unknown(
        self, code: object, log_path: Path
    ) -> None:
        sse.relay(MemoryHandler(), DirectResponse(_error_event(code)), "/dmxapi/v1/responses", 8)

        assert _log_fields(log_path, "sse_terminal")["code"] == "unknown"
        assert "leaked" not in log_path.read_text(encoding="utf-8")

    def test_a_longest_code_token_is_logged(self, log_path: Path) -> None:
        code = "a" + "b_9" * 21
        sse.relay(MemoryHandler(), DirectResponse(_error_event(code)), "/dmxapi/v1/responses", 9)

        assert _log_fields(log_path, "sse_terminal")["code"] == code

    @pytest.mark.parametrize(
        ("events", "last"),
        [
            ((_CREATED, _IN_PROGRESS, _DELTA), "response.output_text.delta"),
            ((_CREATED, _DELTA, b"data: [DONE]\n\n"), "untyped"),
            ((_CREATED, _DELTA, b": keepalive\n\n"), "untyped"),
            ((_DELTA, b'data: {"type":"Response Private-Prose"}\n\n'), "other"),
            ((_DELTA, b'data: {"type":"' + b"a" * 64 + b'"}\n\n'), "a" * 64),
            ((_DELTA, b'data: {"type":"' + b"a" * 65 + b'"}\n\n'), "other"),
            ((_DELTA, b'data: {"delta":"no type"}\n\n'), "untyped"),
            ((), "none"),
        ],
    )
    def test_an_ending_without_a_terminal_event_names_its_last_event_type(
        self, events: tuple[bytes, ...], last: str, log_path: Path
    ) -> None:
        handler = MemoryHandler()

        result = sse.relay(handler, DirectResponse(b"".join(events)), "/dmxapi/v1/responses", 10)

        assert result["result"]["terminal"] is None
        fields = _log_fields(log_path, "sse_end_without_terminal")
        assert fields["last"] == last
        assert fields["events"] == str(len(events))
        assert "Private-Prose" not in log_path.read_text(encoding="utf-8")
        # A truncated stream is never completed for the client.
        assert not handler.output().endswith(_FINAL_CHUNK)

    def test_a_client_reads_the_whole_response_over_a_real_connection(self) -> None:
        # The observed case: the provider refuses the request inside the stream and closes.
        # A client must reach the end of the body; a broken connection reads as a network fault,
        # which a client answers by sending the request again.
        refused = _CREATED + _IN_PROGRESS + _error_event()
        with (
            running_proxy(
                [{"status": 200, "chunks": [refused], "content_type": "text/event-stream"}]
            ) as (port, received),
            request(port, request_body(stream=True)) as response,
        ):
            assert response.status == 200
            downstream = response.read()

        assert downstream == refused
        assert len(received) == 1
