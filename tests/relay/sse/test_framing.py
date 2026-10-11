"""SSE framing contracts."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from types import SimpleNamespace
from typing import cast

import pytest

from openai_responses_proxy.protocol import response as response_projection
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import relay as downstream
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import telemetry
from tests.protocol.test_agent_delivery import _call
from tests.relay.exchange_fixture import DirectResponse
from tests.relay.exchange_fixture import InputTransportFixture
from tests.relay.exchange_fixture import MemoryHandler
from tests.relay.exchange_fixture import request_body
from tests.relay.proxy_fixture import request
from tests.relay.proxy_fixture import running_proxy


class TestSseFraming(InputTransportFixture):
    @pytest.mark.parametrize("chunk_size", [1, 7, 4096])
    @pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete"])
    def test_prelude_commits_once_and_preserves_wire_order(self, chunk_size, terminal) -> None:
        prelude = b': keepalive\n\ndata: {"type":"response.created"}\n\n'
        content = b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
        ending = b'data: {"type":"' + terminal.encode() + b'"}\n\n'
        wire = prelude + content + b": committed keepalive\n\n" + ending
        handler = MemoryHandler()
        header_observations = []
        result = sse._read_one_stream(
            handler,
            DirectResponse(
                *(wire[index : index + chunk_size] for index in range(0, len(wire), chunk_size))
            ),
            lambda: header_observations.append(handler.output()),
        )
        expected = b"".join(
            b"%X\r\n%s\r\n" % (len(event), event)
            for event in (
                b": keepalive\n\n",
                prelude.split(b"\n\n", 1)[1],
                content,
                b": committed keepalive\n\n",
                ending,
            )
        )
        assert header_observations == [b""]
        assert handler.output() == expected
        assert result["terminal"] == terminal
        assert result["events"] == 5
        assert result["wrote_downstream"] is True

    def test_terminal_stream_finishes_while_chunked_upstream_remains_open(self, *, mocker):
        completed = b'data: {"type":"response.completed"}\n\n'
        release = threading.Event()
        mocker.patch.object(sse, "UPSTREAM_READ_TIMEOUT", 1.0)
        mocker.patch.object(sse.time, "sleep", return_value=None)
        with running_proxy([{"chunks": [completed], "stall_event": release}]) as (port, received):
            try:
                with request(port, request_body(stream=True)) as response:
                    assert response.status == 200
                    assert completed in response.read()
                    assert not release.is_set()
            finally:
                release.set()
        assert len(received) == 1

    def test_malformed_committed_stream_remains_a_truncated_http_response(self):
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        with (
            running_proxy([{"chunks": [content, b'data: {"type":\n\n']}]) as (
                port,
                received,
            ),
            request(port, request_body(stream=True)) as response,
        ):
            assert response.status == 200
            with pytest.raises(http.client.IncompleteRead) as raised:
                response.read()
            assert content in raised.value.partial
            assert b"503" not in raised.value.partial
            assert b"stream_projection_failed" not in raised.value.partial
        assert len(received) == 1

    @pytest.mark.parametrize(
        "terminal", ["response.completed", "response.incomplete", "response.failed"]
    )
    def test_terminal_event_finishes_without_reading_or_forwarding_trailing_data(
        self, terminal, *, mocker
    ) -> None:
        event = b"data: " + json.dumps({"type": terminal}).encode() + b"\n\n"
        tail = b'data: {"type":"response.output_text.delta","delta":"after-terminal"}\n\n'
        upstream = DirectResponse(event + tail, TimeoutError("must not await upstream EOF"))
        read = mocker.spy(upstream, "read")
        close = mocker.spy(upstream, "close")
        handler = MemoryHandler()

        result = sse.relay(handler, upstream, "/v1/responses", 1)

        read.assert_called_once_with(8192)
        close.assert_called_once_with()
        assert result["result"] is not None
        assert result["result"]["terminal"] == terminal
        assert result["result"]["error"] is None
        assert event in handler.output()
        assert b"after-terminal" not in handler.output()

    @pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, RuntimeError])
    def test_downstream_failure_releases_owned_upstream_without_reconnect(
        self, failure, *, mocker
    ) -> None:
        upstream = DirectResponse(b'data: {"type":"response.completed"}\n\n')
        close = mocker.spy(upstream, "close")
        handler = MemoryHandler()
        mocker.patch.object(handler.wfile, "write", side_effect=failure("private-detail"))
        reopen = mocker.Mock()

        with pytest.raises(failure):
            sse.relay(handler, upstream, "/v1/responses", 1, reopen=reopen)

        close.assert_called_once_with()
        reopen.assert_not_called()

    def test_malformed_event_after_commit_closes_without_a_second_http_response(
        self, *, mocker
    ) -> None:
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        upstream = DirectResponse(content, b'data: {"type":\n\n')
        close = mocker.spy(upstream, "close")
        handler = MemoryHandler()
        exchange = mocker.Mock(handler=handler, request_id=1, used_input_variant_dialogue=False)

        downstream.relay_sse(exchange, upstream)

        assert handler.statuses == [200]
        assert handler.close_connection
        assert content in handler.output()
        assert b"stream_projection_failed" not in handler.output()
        assert not handler.output().endswith(b"0\r\n\r\n")
        close.assert_called_once_with()
        exchange.upstream.assert_not_called()

    def test_direct_sse_reader_covers_sanitization_and_failure_details(self) -> None:
        encrypted = (
            b'data: {"type":"response.output_item.added","item":'
            b'{"type":"reasoning","encrypted_content":"secret"}}\n\n'
        )
        cases = (
            (
                DirectResponse(
                    http.client.IncompleteRead(b'data: {"type": "response.incomplete"}')
                ),
                "response.incomplete",
                "incomplete",
                object,
            ),
            (
                DirectResponse(TimeoutError("private")),
                None,
                "timeout",
                socket.timeout,
            ),
            (DirectResponse(RuntimeError("private")), None, "eof", RuntimeError),
            (DirectResponse(encrypted), None, "eof", object),
        )
        results = []
        final_output = b""
        for upstream, _, _, error_type in cases:
            admission.reset_for_test()
            telemetry.reset_for_test()
            cooldown.reset_for_test()
            handler = MemoryHandler()
            result = sse._read_one_stream(handler, upstream, lambda: None)
            results.append(
                (
                    result["terminal"],
                    result["detail"],
                    isinstance(result["error"], error_type),
                )
            )
            final_output = handler.output()
        assert results == [(terminal, detail, True) for _, terminal, detail, _ in cases]
        assert b"secret" in final_output

    def test_live_sse_preserves_ciphertext_after_commit(self, *, mocker) -> None:
        plaintext = b'data: {"type":"response.output_text.delta","delta":"visible"}\n\n'
        encrypted = (
            b'data: {"type":"response.completed","response":{"output":['
            b'{"type":"reasoning","encrypted_content":"secret"}]}}\n\n'
        )
        serializer = mocker.Mock(side_effect=TypeError("unsupported"))
        mocker.patch.object(
            response_projection, "json", SimpleNamespace(loads=json.loads, dumps=serializer)
        )
        handler = MemoryHandler()
        result = sse.relay(
            handler,
            DirectResponse(plaintext + encrypted),
            "/dmxapi/v1/responses",
            1,
            send_headers=lambda: None,
        )

        assert not result["pre_content_exhausted"]
        assert result["result"] is not None
        assert result["result"]["detail"] == "completed"
        assert result["result"]["error"] is None
        assert b"visible" in handler.output()
        assert b"secret" in handler.output()
        assert handler.output().endswith(b"0\r\n\r\n")
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["stream_projection_failures"] == 0
        serializer.assert_not_called()

    def test_live_sse_preserves_ciphertext_before_commit(self, *, mocker) -> None:
        body = request_body(stream=True)
        encrypted = (
            b'data: {"type":"response.completed","response":{"output":['
            b'{"type":"reasoning","encrypted_content":"secret"}]}}\n\n'
        )
        with running_proxy(
            [
                {
                    "status": 200,
                    "chunks": [encrypted],
                    "content_type": "text/event-stream",
                }
            ]
        ) as (port, _received):
            serializer = mocker.Mock(side_effect=TypeError("unsupported"))
            mocker.patch.object(
                response_projection, "json", SimpleNamespace(loads=json.loads, dumps=serializer)
            )
            with request(port, body) as response:
                status = response.status
                payload = response.read()

        assert status == 200
        assert b"secret" in payload
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["stream_projection_failures"] == 0
        serializer.assert_not_called()

    def test_malformed_sse_fails_before_downstream_commit(self) -> None:
        malformed = b'data: {"type":\n\n'
        handler = MemoryHandler()

        result = sse.relay(
            handler,
            DirectResponse(malformed),
            "/dmxapi/v1/responses",
            1,
            send_headers=lambda: None,
        )

        assert result["pre_content_exhausted"]
        assert result["result"] is not None
        assert result["result"]["detail"] == "projection_failed"
        assert handler.output() == b""
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["stream_projection_failures"] == 1

    @pytest.mark.parametrize("failure", ["event", "plaintext", "unknown"])
    def test_committed_validation_failure_reports_owned_reason_without_replay(
        self, failure: str, *, mocker
    ) -> None:
        content = b'data: {"type":"response.output_text.delta","delta":"visible"}\n\n'
        if failure == "plaintext":
            call = _call(message="gAAAA-private-message")
            rejected = (
                b"data: "
                + json.dumps({"type": "response.output_item.done", "item": call}).encode()
                + b"\n\n"
            )
            expected_reason = "agent_delivery_plaintext_unproved"
        else:
            rejected = b'data: {"type":"private-invalid-json"\n\n'
            expected_reason = "invalid_responses_event"
        if failure == "unknown":
            restore = response_projection.sse_event_data

            def unclassified_event(raw: bytes) -> object:
                if raw == rejected:
                    raise ValueError("private-unclassified-error")
                return restore(raw)

            mocker.patch.object(response_projection, "sse_event_data", unclassified_event)
            expected_reason = "unknown"
        logger = mocker.patch.object(operational_log, "log")
        reopen = mocker.Mock()
        handler = MemoryHandler()
        result = sse.relay(
            handler,
            DirectResponse(content + rejected),
            "/ucloud/v1/responses?prompt=private-query",
            40032,
            send_headers=lambda: None,
            reopen=reopen,
            plaintext_agent_delivery=failure == "plaintext",
        )

        assert result["attempts"] == 1
        assert result["result"] is not None
        assert result["result"]["detail"] == "projection_failed"
        assert result["result"]["failure_code"] == expected_reason
        assert not result["pre_content_exhausted"]
        assert handler.output() == b"%X\r\n%s\r\n" % (len(content), content)
        assert handler.close_connection
        reopen.assert_not_called()
        message = logger.call_args.args[0]
        assert f"reason={expected_reason}" in message
        assert "path=/ucloud/v1/responses" in message
        assert "private" not in message
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["stream_projection_failures"] == 1

    def test_public_sse_relay_rejects_an_unprojectable_stream(self, *, mocker) -> None:
        handler = MemoryHandler()
        exchange = mocker.Mock(
            handler=handler,
            request_id=7,
            used_input_variant_dialogue=False,
        )
        mocker.patch.object(
            sse,
            "relay",
            return_value={
                "result": {"detail": "projection_failed"},
                "pre_content_exhausted": True,
                "attempts": 1,
            },
        )

        downstream.relay_sse(exchange, DirectResponse())

        assert handler.statuses == [503]
        assert b"stream_projection_failed" in handler.output()
        exchange.log.assert_called_once_with("sse_projection_failed")

    def test_unexpected_terminal_validator_failure_is_bounded(self, *, mocker) -> None:
        handler = MemoryHandler()
        exchange = mocker.Mock(handler=handler)
        mocker.patch.object(
            response_projection,
            "validate_json_response",
            side_effect=RuntimeError("private-validator-detail"),
        )

        downstream.relay_responses_json(
            exchange,
            DirectResponse(b'{"status":"completed","output":[]}'),
        )

        assert handler.statuses == [503]
        payload = json.loads(handler.output())
        assert payload["error"]["code"] == "invalid_responses_success_body"
        assert payload["error"]["reason"] == "RuntimeError"
        exchange.log.assert_called_once_with(
            "invalid_responses_success_body", "reason=RuntimeError "
        )

    def test_direct_sse_relay_flushes_completed_prelude(self) -> None:
        handler = MemoryHandler()
        sent = 0

        def mark_headers() -> None:
            nonlocal sent
            sent += 1

        result = sse.relay(
            handler,
            DirectResponse(
                b'data: {"type":"response.created"}\n\ndata: {"type":"response.completed"}\n\n'
            ),
            "/v1/responses",
            1,
            send_headers=mark_headers,
        )
        assert not result["pre_content_exhausted"]
        assert sent == 1
        assert handler.output().count(b"response.completed") == 1
