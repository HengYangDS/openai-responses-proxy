"""SSE recovery contracts."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import EXACT_ERROR
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import request
from tests.relay.fixtures import request_body
from tests.relay.fixtures import running_proxy


class TestSseRecovery(InputTransportFixture):
    @pytest.mark.parametrize(
        "control", [b"data: [DONE]\n\n", b": keepalive\n\n", b"event: ping\n\n"]
    )
    def test_pre_content_failure_controls_preserve_reconnect(self, control, *, mocker) -> None:
        failed = (
            b'data:{"type" : "response.created"}\n\n'
            b'data:{"type" : "response.in_progress"}\n\n'
            b'data:{"type" : "response.failed",'
            b'"response":{"error":{"code":"server_error"}}}\n\n' + control
        )
        completed = b'data: {"type":"response.completed"}\n\n'
        handler = MemoryHandler()
        reopen = mocker.Mock(return_value=DirectResponse(completed))
        mocker.patch.object(sse.time, "sleep", return_value=None)
        mocker.patch.object(sse.time, "monotonic", return_value=100.0)

        result = sse.relay(
            handler, DirectResponse(failed), "/ucloud/v1/responses", 1, reopen=reopen
        )

        assert result["attempts"] == 2
        reopen.assert_called_once_with(100.0 + sse.UPSTREAM_TIMEOUT)
        assert b"response.failed" not in handler.output()
        assert handler.output().count(b"response.completed") == 1

    @pytest.mark.parametrize("event", ["response.output_text.delta", "response.output_item.added"])
    def test_committed_output_is_never_replayed(self, event, *, mocker) -> None:
        content = json.dumps(
            {"type": event, "delta": "keep", "item": {"type": "function_call"}}
        ).encode()
        raw = (
            b"data: " + content + b"\n\n"
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
            b"data: [DONE]\n\n"
        )
        handler = MemoryHandler()
        reopen = mocker.Mock()

        result = sse.relay(handler, DirectResponse(raw), "/ucloud/v1/responses", 1, reopen=reopen)

        assert result["attempts"] == 1
        reopen.assert_not_called()
        assert content in handler.output()
        assert b"response.failed" in handler.output()

    @pytest.mark.parametrize("code", ["invalid_prompt", "rate_limit_exceeded", "unknown"])
    def test_permanent_failure_is_preserved_without_reconnect(self, code, *, mocker) -> None:
        raw = (
            b"data: "
            + json.dumps(
                {"type": "response.failed", "response": {"error": {"code": code}}}
            ).encode()
            + b"\n\n"
        )
        handler = MemoryHandler()
        reopen = mocker.Mock()

        result = sse.relay(handler, DirectResponse(raw), "/ucloud/v1/responses", 1, reopen=reopen)

        assert result["attempts"] == 1
        reopen.assert_not_called()
        assert raw in handler.output()

    def test_failed_response_with_output_is_not_replayed(self, *, mocker) -> None:
        raw = b'data: {"type":"response.failed","response":{"output":[{"type":"function_call"}],"error":{"code":"server_error"}}}\n\n'
        handler = MemoryHandler()
        reopen = mocker.Mock(return_value=DirectResponse())
        mocker.patch.object(sse.time, "sleep", return_value=None)

        result = sse.relay(handler, DirectResponse(raw), "/ucloud/v1/responses", 1, reopen=reopen)

        assert result["attempts"] == 1
        reopen.assert_not_called()
        assert raw in handler.output()

    def test_failure_diagnostics_correlate_without_disclosing_payload(self, *, mocker) -> None:
        request_id = "64781e82-1e8f-42af-a06a-409e154bbfe2"
        raw = (
            b"data: "
            + json.dumps(
                {
                    "type": "response.failed",
                    "response": {
                        "error": {
                            "code": "server_error",
                            "message": f"private-prompt-secret request ID {request_id} in your email",
                        }
                    },
                }
            ).encode()
            + b"\n\n"
        )
        mocker.patch.object(sse.time, "sleep", return_value=None)
        reopen = mocker.Mock(
            return_value=DirectResponse(b'data: {"type":"response.completed"}\n\n')
        )

        sse.relay(MemoryHandler(), DirectResponse(raw), "/ucloud/v1/responses", 7, reopen=reopen)

        logs = Path(operational_log.LOG_PATH).read_text()
        assert f"upstream_request_id={request_id}" in logs
        assert "code=server_error" in logs
        assert "req=7 event=sse_upstream_failed" in logs
        assert "private-prompt-secret" not in logs

    def test_reconnect_backoff_does_not_outlive_stream_deadline(self, *, mocker) -> None:
        handler = MemoryHandler()
        reopen = mocker.Mock(return_value=DirectResponse())
        mocker.patch.object(
            sse,
            "_read_one_stream",
            return_value={
                "terminal": None,
                "events": 0,
                "wrote_downstream": False,
                "detail": "eof",
                "error": None,
            },
        )
        sleep = mocker.patch.object(sse.time, "sleep", return_value=None)
        mocker.patch.object(
            sse.time, "monotonic", side_effect=[0.0] + [sse.UPSTREAM_TIMEOUT - 0.1] * 20
        )

        result = sse.relay(handler, DirectResponse(), "/ucloud/v1/responses", 1, reopen=reopen)

        assert result["attempts"] == 1
        sleep.assert_not_called()
        reopen.assert_not_called()

    def test_transient_failure_reconnects_are_bounded(self, *, mocker) -> None:
        raw = (
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
            b"data: [DONE]\n\n"
        )
        handler = MemoryHandler()
        reopen = mocker.Mock(side_effect=lambda _deadline: DirectResponse(raw))
        mocker.patch.object(sse.time, "sleep", return_value=None)

        result = sse.relay(handler, DirectResponse(raw), "/ucloud/v1/responses", 1, reopen=reopen)

        assert result["pre_content_exhausted"]
        assert result["attempts"] == 6
        assert reopen.call_count == 5
        assert handler.output() == b""

    def test_recovered_sse_failure_does_not_reconnect(self, *, mocker) -> None:
        body = request_body(stream=True)
        incomplete = b'data: {"type":"response.created"}\n\n'
        unexpected_reconnect = b'data: {"type":"response.completed"}\n\n'
        with running_proxy(
            [
                (400, EXACT_ERROR),
                {
                    "status": 200,
                    "payload": incomplete,
                    "content_type": "text/event-stream",
                },
                {
                    "status": 200,
                    "payload": unexpected_reconnect,
                    "content_type": "text/event-stream",
                },
            ]
        ) as (port, received):
            mocker.patch.object(sse.time, "sleep", return_value=None)
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                payload = json.loads(error.read())
                assert error.code == 503
                assert payload["error"]["code"] == "stream_pre_content_exhausted"
                assert payload["error"]["attempts"] == 1

        assert len(received) == 2
        counters, _classifications = self._status_maps()
        assert counters["streams_pre_content_reconnect_attempts"] == 0
        assert counters["input_variant_dialogue_recovery_exhausted"] == 1
        assert counters["input_variant_dialogue_recovery_accepted"] == 0

    def test_recovered_sse_completes_once_without_reconnect(self) -> None:
        body = request_body(stream=True)
        completed = (
            b'data: {"type":"response.created"}\n\n'
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
            b'data: {"type":"response.completed"}\n\n'
        )
        with running_proxy(
            [
                (400, EXACT_ERROR),
                {
                    "status": 200,
                    "chunks": [completed],
                    "content_type": "text/event-stream",
                },
            ]
        ) as (port, received):
            with request(port, body) as response:
                assert response.status == 200
                downstream = response.read()
            logs = Path(operational_log.LOG_PATH).read_text(encoding="utf-8")

        assert len(received) == 2
        assert downstream.count(b'"type":"response.created"') == 1
        assert downstream.count(b'"type":"response.output_text.delta"') == 1
        assert downstream.count(b'"type":"response.completed"') == 1
        assert "private-current-prompt" not in logs
        assert "stale-conversation-binding" not in logs
        counters, classifications = self._status_maps()
        assert counters["streams_pre_content_reconnect_attempts"] == 0
        assert counters["input_variant_dialogue_recovery_attempts"] == 1
        assert counters["input_variant_dialogue_recovery_accepted"] == 1
        assert counters["input_variant_dialogue_recovery_exhausted"] == 0
        assert classifications == {"input_variant_validation_error": 1}

    def test_direct_sse_relay_handles_reopen_failure_and_incomplete_terminal(
        self, *, mocker
    ) -> None:
        failed = DirectResponse(
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
        )
        handler = MemoryHandler()
        mocker.patch.object(sse.time, "sleep", return_value=None)
        result = sse.relay(
            handler,
            failed,
            "/v1/responses",
            1,
            reopen=mocker.Mock(side_effect=OSError("private")),
        )
        assert result["pre_content_exhausted"]
        assert result["attempts"] == 2

        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        handler = MemoryHandler()
        result = sse.relay(
            handler,
            DirectResponse(b'data: {"type": "response.incomplete"}\n\n'),
            "/v1/responses",
            2,
            send_headers=lambda: None,
        )
        assert not result["pre_content_exhausted"]
        assert handler.output().endswith(b"0\r\n\r\n")
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["streams_incomplete"] == 1

    def test_sse_deadline_and_reconnect_deadline_are_bounded(self, *, mocker) -> None:
        handler = MemoryHandler()
        result = sse._read_one_stream(
            handler, DirectResponse(), lambda: None, deadline=sse.time.monotonic() - 1
        )
        assert result["detail"] == "deadline"

        handler = MemoryHandler()
        mocker.patch.object(
            sse,
            "_read_one_stream",
            return_value={
                "terminal": None,
                "events": 0,
                "wrote_downstream": False,
                "detail": "deadline",
                "error": None,
            },
        )
        mocker.patch.object(sse.time, "monotonic", side_effect=[0.0, sse.UPSTREAM_TIMEOUT])
        result = sse.relay(
            handler,
            DirectResponse(b""),
            "/v1/responses",
            2,
            reopen=mocker.Mock(),
        )
        assert result["attempts"] == 1

        mocker.stopall()
        handler = MemoryHandler()
        mocker.patch.object(sse.time, "monotonic", return_value=2.0)
        result = sse._read_one_stream(
            handler,
            DirectResponse(TimeoutError("late")),
            lambda: None,
            deadline=1.0,
        )
        assert result["detail"] == "deadline"
