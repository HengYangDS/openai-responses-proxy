"""Bound pending SSE data without capping a valid stream's total size."""

from __future__ import annotations

import json

import pytest

from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import sse
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler

LIMIT = 4096
CONTENT = b'data: {"type":"response.output_text.delta","delta":"visible"}\n\n'
COMPLETED = b'data: {"type":"response.completed"}\n\n'


class TestSseBuffering(InputTransportFixture):
    @pytest.mark.parametrize(
        ("shape", "committed"),
        [
            ("partial", False),
            ("partial", True),
            ("event", False),
            ("event", True),
            ("comments", False),
            ("created", False),
            ("tail", False),
            ("failed", False),
        ],
    )
    def test_pending_bytes_fail_without_replay_or_successful_termination(
        self, shape, *, committed, mocker
    ) -> None:
        mocker.patch.object(sse, "SSE_BUFFER_MAX_BYTES", LIMIT, create=True)
        oversized = b'data: {"type":"response.output_text.delta","delta":"' + b"x" * LIMIT
        if shape == "event":
            oversized += b'"}\n\n'
        fragments = [oversized[index : index + 127] for index in range(0, len(oversized), 127)]
        if shape in {"comments", "created", "tail", "failed"}:
            held = (
                b": keepalive\n\n"
                if shape == "comments"
                else b'data: {"type":"response.created"}\n\n'
            )
            fragments = [held] * (LIMIT // len(held) + 1)
            if shape in {"tail", "failed"}:
                fragments = [held] * (LIMIT // len(held))
                fragments.append(
                    b": tail"
                    if shape == "tail"
                    else (
                        b'data: {"type":"response.failed","response":{"error":'
                        b'{"code":"server_error"}}}\n\n'
                    )
                )
        trailing = [] if shape == "tail" else [COMPLETED]
        upstream = DirectResponse(*([CONTENT] if committed else []), *fragments, *trailing)
        close = mocker.spy(upstream, "close")
        reopen = mocker.Mock()
        headers = mocker.Mock()
        handler = MemoryHandler()

        outcome = sse.relay(
            handler, upstream, "/v1/responses", 1, reopen=reopen, send_headers=headers
        )

        stream = outcome["result"]
        assert stream is not None
        assert stream["detail"] == "buffer_limit"
        assert stream["failure_code"] == "stream_buffer_limit"
        assert stream["terminal"] is None
        assert outcome["attempts"] == 1
        assert outcome["pre_content_exhausted"] is not committed
        assert headers.call_count == int(committed)
        assert (CONTENT in handler.output()) is committed
        assert COMPLETED not in handler.output()
        assert (getattr(handler, "close_connection", False)) is committed
        assert not handler.output().endswith(b"0\r\n\r\n")
        close.assert_called_once_with()
        reopen.assert_not_called()

    def test_individually_bounded_events_do_not_cap_the_total_stream(self, *, mocker) -> None:
        mocker.patch.object(sse, "SSE_BUFFER_MAX_BYTES", LIMIT, create=True)
        prefix = b'data: {"type":"response.output_text.delta","delta":"'
        ending = b'"}\r\n\r\n'
        event = prefix + b"x" * (LIMIT - len(prefix) - len(ending)) + ending
        wire = event * 3 + COMPLETED
        upstream = DirectResponse(
            *(wire[index : index + 127] for index in range(0, len(wire), 127))
        )
        handler = MemoryHandler()

        outcome = sse.relay(handler, upstream, "/v1/responses", 1)

        assert outcome["result"] is not None
        assert outcome["result"]["detail"] == "completed"
        assert handler.output().count(event) == 3
        assert handler.output().endswith(b"0\r\n\r\n")

    def test_precommit_limit_returns_one_bounded_http_error(self, *, mocker) -> None:
        mocker.patch.object(sse, "SSE_BUFFER_MAX_BYTES", LIMIT, create=True)
        handler = MemoryHandler()
        exchange = mocker.Mock(
            handler=handler,
            request_id=1,
            deadline=float("inf"),
            used_input_variant_dialogue=False,
            plaintext_agent_delivery=False,
        )
        upstream = DirectResponse(b"data: " + b"x" * LIMIT)

        downstream.relay_sse(exchange, upstream)

        assert handler.statuses == [503]
        assert json.loads(handler.output())["error"]["code"] == "stream_buffer_limit"
        assert b"x" * 32 not in handler.output()
        exchange.upstream.assert_not_called()
