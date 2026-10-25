"""Pre-content stream recovery and committed-byte boundaries."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import cast

import pytest

from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import LoopbackTransportFixture
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy


class TestProxyTransport(LoopbackTransportFixture):
    def test_reconnects_a_pre_content_response_failed_stream(self, *, mocker):
        failed = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n',
                b"data: [DONE]\n\n",
            ],
        }
        recovered = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ],
        }
        body = json.dumps({"stream": True, "input": []}).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        received, payload = self.exchange([failed, recovered], body)

        assert len(received) == 2
        assert b"recovered" in payload
        assert payload.count(b"response.created") == 1
        status = proxy.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["streams_pre_content_reconnect_attempts"] == 1
        assert counters["streams_completed"] == 1

    def test_normalizes_exhausted_pre_content_sse_failures_to_retryable_503(self, *, mocker):
        failed = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n',
                b"data: [DONE]\n\n",
            ],
        }
        body = json.dumps({"stream": True, "input": []}).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)

        with running_proxy([failed] * 6) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert len(set(received)) == 1
        assert json.loads(received[0]) == {**json.loads(body), "store": False}
        assert payload["error"]["type"] == "upstream_unavailable"
        assert payload["error"]["code"] == "stream_pre_content_exhausted"
        assert payload["error"]["attempts"] == 6
        status = proxy.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        last_failure = cast("dict[str, object]", status["last_failure"])
        assert counters["streams_pre_content_reconnect_attempts"] == 5
        assert counters["streams_pre_content_exhausted"] == 1
        assert counters["streams_failed"] == 1
        assert last_failure["classification"] == "stream_pre_content_exhausted"

    def test_does_not_reconnect_after_downstream_stream_bytes_are_committed(self):
        partial = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
            ],
        }
        unexpected_retry = {
            "chunks": [b'data: {"type":"response.completed"}\n\n'],
        }
        body = json.dumps({"stream": True, "input": []}).encode()

        with (
            running_proxy([partial, unexpected_retry]) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            with pytest.raises(http.client.IncompleteRead) as raised:
                response.read()
            payload = raised.value.partial

        assert len(received) == 1
        assert b"partial" in payload
        status = proxy.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["streams_pre_content_reconnect_attempts"] == 0
        assert counters["streams_failed"] == 1
