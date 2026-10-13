"""HTTP dispatch, cooldown, retry, SSE, and disconnect contracts for empty responses."""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.providers.policies import dmxapi as policy
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import request
from tests.relay.fixtures import serve_proxy

ROOT = Path(__file__).resolve().parents[2]
EMPTY_RESPONSE = (
    b'{"error":{"message":"official provider returned an empty response",'
    b'"type":"dmx_api_error","code":"empty_response"}}'
)
UNKNOWN_477 = b'{"error":{"type":"dmx_api_error","code":"other"}}'
SUCCESS = b'{"id":"resp_recovered","status":"completed"}'


def body(payload: object) -> bytes:
    """Encode one deterministic UTF-8 JSON request body."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def semantic_body() -> bytes:
    """Return representative history crossing every projection owner."""
    return body(
        {
            "previous_response_id": "stale",
            "conversation": {"id": "stale"},
            "prompt_cache_key": "stale",
            "include": ["reasoning.encrypted_content", "other"],
            "input": [
                {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
                {"type": "message", "role": "developer", "content": "policy"},
                {
                    "type": "agent_message",
                    "author": "planner",
                    "recipient": "user",
                    "content": [{"type": "output_text", "text": "plan"}],
                },
                {
                    "type": "function_call",
                    "call_id": "f",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "f", "output": "ok"},
                {
                    "type": "custom_tool_call",
                    "call_id": "c",
                    "name": "shell",
                    "input": "pwd",
                },
                {"type": "custom_tool_call_output", "call_id": "c", "output": "here"},
                {"type": "message", "role": "user", "content": "continue"},
            ],
        }
    )


class EmptyResponseTransportTests:
    """Exercise one-shot recovery through real loopback HTTP boundaries."""

    def setup_method(self) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()

    _body = staticmethod(body)
    _request = staticmethod(request)

    @staticmethod
    def _remember_failure(key, *, now=None):
        cooldown.remember_failure(
            key,
            capacity=policy.FAILURE_CACHE_CAPACITY,
            cooldown_seconds=policy.FAILURE_COOLDOWN_SECONDS,
            now=now,
        )

    @staticmethod
    def _cooldown_remaining(key, *, now=None):
        return cooldown.remaining(key, now=now)

    def _read_http_error(self, port, request_body):
        with pytest.raises(urllib.error.HTTPError) as raised:
            self._request(port, request_body)
        error = raised.value
        with error:
            payload = error.read()
            headers = error.headers
            code = error.code
        return code, headers, payload

    def test_classified_477_dispatches_policy_fallback_once(self):
        body = semantic_body()
        _projection = rewrite.sanitize_responses_body(body)
        sanitized = _projection.body
        _ = _projection.diagnostic()

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        assert received == [sanitized, sanitized]
        fallback_payload = json.loads(received[1])
        assistant = next(
            item for item in fallback_payload["input"] if item.get("role") == "assistant"
        )
        assert isinstance(assistant["content"], str)

    def test_encrypted_only_agent_history_is_projected_once_then_retried_exactly(self):
        secret = "provider-bound-agent-state"
        request_body = self._body(
            {
                "input": [
                    {
                        "type": "agent_message",
                        "author": "planner",
                        "recipient": "user",
                        "content": [],
                        "encrypted_content": secret,
                    }
                ]
            }
        )
        _projection = rewrite.sanitize_responses_body(request_body)
        sanitized = _projection.body
        note = _projection.diagnostic()
        assert sanitized is not None, note

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, request_body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        assert received == [sanitized, sanitized]
        assert json.loads(received[1])["input"] == json.loads(request_body)["input"]

    @pytest.mark.parametrize(
        "image_url",
        ["https://example.test/a.png", "data:image/png;base64,cGl4ZWxz"],
    )
    def test_classified_477_retries_portable_images(self, image_url: str) -> None:
        image = {
            "type": "input_image",
            "image_url": image_url,
            "detail": "auto",
        }
        request_body = self._body(
            {
                "stream": False,
                "input": [
                    {"type": "message", "role": "user", "content": [image]},
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "inspect",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": [image],
                    },
                ],
            }
        )
        _projection = rewrite.sanitize_responses_body(request_body)
        sanitized = _projection.body
        note = _projection.diagnostic()
        assert sanitized is not None, note

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, request_body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        assert received == [sanitized, sanitized]
        replay = json.loads(received[1])
        assert replay["input"][0]["content"] == [image]
        assert replay["input"][2]["output"] == [image]

    def test_unsafe_projection_returns_local_400_without_an_upstream_call(self):
        body = self._body({"stream": False, "input": [{"type": "future_item"}]})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 4, tmp)
            try:
                code, headers, raw = self._read_http_error(port, body)
            finally:
                cleanup()

        assert code == 400
        assert headers.get("Retry-After") is None
        assert json.loads(raw)["error"]["code"] == "provider_portable_projection_rejected"
        assert received == []

    def test_search_history_is_removed_before_dmx_fallback(self):
        payload = {
            "previous_response_id": "stale",
            "prompt_cache_key": "stale-cache",
            "input": [
                {"type": "message", "role": "system", "content": "policy"},
                {"type": "web_search_call", "action": {"type": "search"}},
                {"type": "message", "role": "developer", "content": "rules"},
                {"type": "tool_search_call", "call_id": "s", "execution": "server"},
                {"type": "tool_search_output", "call_id": "s", "execution": "server"},
                {"type": "message", "role": "assistant", "content": "old answer"},
                {"type": "message", "role": "user", "content": "current"},
            ],
        }
        body = self._body(payload)
        _projection = rewrite.sanitize_responses_body(body)
        sanitized = _projection.body
        _ = _projection.diagnostic()
        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()
        assert received == [sanitized, sanitized]
        forwarded = json.loads(received[0])
        assert "previous_response_id" not in forwarded
        assert "prompt_cache_key" not in forwarded
        assert not {"web_search_call", "tool_search_call", "tool_search_output"} & {
            item["type"] for item in forwarded["input"]
        }

    def test_rejected_projection_returns_400_without_an_upstream_fallback(self, subtests):
        cases = (
            (
                [
                    {"type": "future_item", "opaque": "x"},
                    {"type": "message", "role": "user", "content": "current"},
                ],
                "unknown_item_type",
            ),
            (
                [
                    {"type": "web_search_call", "action": {"type": "search"}},
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_image", "image_url": "x"}],
                    },
                ],
                "empty_portable_content",
            ),
        )
        for items, expected_reason in cases:
            with subtests.test(items=items), tempfile.TemporaryDirectory() as tmp:
                admission.reset_for_test()
                telemetry.reset_for_test()
                cooldown.reset_for_test()
                port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 4, tmp)
                try:
                    code, _, raw = self._read_http_error(port, self._body({"input": items}))
                finally:
                    cleanup()
                assert code == 400
                error = json.loads(raw)["error"]
                assert error["code"] == "provider_portable_projection_rejected"
                assert error["reason"] == expected_reason
                assert received == []

    def test_fallback_failure_is_terminal_after_exactly_two_upstream_attempts(self, subtests):
        body = self._body(
            {
                "stream": False,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        cases = (
            (400, b'{"error":{"code":"bad_request"}}'),
            (477, EMPTY_RESPONSE),
            (500, b'{"error":{"code":"upstream_failure"}}'),
        )
        for second_status, second_payload in cases:
            with subtests.test(second_status=second_status):
                admission.reset_for_test()
                telemetry.reset_for_test()
                cooldown.reset_for_test()
                responses = [(477, EMPTY_RESPONSE), (second_status, second_payload)]
                responses += [(second_status, second_payload)] * (3 * (second_status in (477, 500)))
                with tempfile.TemporaryDirectory() as tmp:
                    port, received, cleanup = serve_proxy(responses, tmp)
                    try:
                        code, _headers, raw = self._read_http_error(port, body)
                    finally:
                        cleanup()
                assert code == 503
                assert len(received) == 2
                assert json.loads(raw)["error"]["attempts"] == 2

    def test_unknown_477_is_passed_through_without_fallback_or_cooldown(self):
        body = self._body({"stream": False, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, UNKNOWN_477)], tmp)
            try:
                code, _headers, raw = self._read_http_error(port, body)
            finally:
                cleanup()

        assert code == 477
        assert raw == UNKNOWN_477
        projected = self._body({"stream": False, "input": [], "store": False})
        assert received == [projected]
        key = policy.request_fingerprint(projected)
        assert self._cooldown_remaining(key, now=100.0) == 0

    def test_streaming_request_recovers_only_before_any_sse_bytes(self):
        body = self._body(
            {
                "stream": True,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        recovered = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), recovered], tmp)
            try:
                with self._request(port, body) as response:
                    payload = response.read()
            finally:
                cleanup()

        assert response.status == 200
        assert b'"delta":"ok"' in payload
        assert len(received) == 2
        assert received[1] != body

    def test_streaming_fallback_exhaustion_is_standard_http_503(self):
        body = self._body({"stream": True, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 4, tmp)
            try:
                code, headers, raw = self._read_http_error(port, body)
            finally:
                cleanup()

        assert code == 503
        assert headers["Content-Type"] == "application/json"
        assert headers["Retry-After"] == "3"
        assert json.loads(raw)["error"]["attempts"] == 2
        assert len(received) == 2

    def test_failed_recovery_cools_identical_request_without_upstream_replay(self):
        body = self._body({"stream": False, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 8, tmp)
            try:
                first = self._read_http_error(port, body)
                second = self._read_http_error(port, body)
            finally:
                cleanup()

        assert first[0] == 503
        assert second[0] == 503
        assert json.loads(second[2])["error"]["attempts"] == 0
        assert len(received) == 2
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_cooldown_hits"] == 1

    def test_cooldown_ttl_capacity_and_concurrent_access_are_bounded(self):
        first = self._body(
            {
                "previous_response_id": "first",
                "input": [{"type": "message", "role": "user", "content": "same"}],
            }
        )
        second = self._body(
            {
                "previous_response_id": "second",
                "input": [{"type": "message", "role": "user", "content": "same"}],
            }
        )
        first_key = policy.request_fingerprint(first)
        second_key = policy.request_fingerprint(second)
        self._remember_failure(first_key, now=10.0)
        assert self._cooldown_remaining(first_key, now=10.1) > 0
        assert self._cooldown_remaining(second_key, now=10.1) == 0

        self._remember_failure("expires", now=100.0)
        assert self._cooldown_remaining("expires", now=100.1) > 0
        assert cooldown.has_failure_for_test("expires")
        past_ttl = 100.0 + policy.FAILURE_COOLDOWN_SECONDS + 0.1
        assert self._cooldown_remaining("expires", now=past_ttl) == 0
        # The TTL purge is a real eviction, not merely a read-time comparison:
        # the reading call above must have removed the expired entry itself.
        assert not cooldown.has_failure_for_test("expires")

        # A stale entry is also purged by the next *write* to an unrelated key.
        self._remember_failure("stale", now=300.0)
        self._remember_failure("other", now=300.0 + policy.FAILURE_COOLDOWN_SECONDS + 0.1)
        assert not cooldown.has_failure_for_test("stale")
        assert cooldown.has_failure_for_test("other")

        def remember(index):
            key = f"key-{index}"
            self._remember_failure(key, now=200.0 + index / 10_000)
            return self._cooldown_remaining(key, now=200.0)

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(remember, range(policy.FAILURE_CACHE_CAPACITY + 100)))
        assert cooldown.failure_count_for_test() <= policy.FAILURE_CACHE_CAPACITY
        assert not cooldown.has_failure_for_test("key-0")

    def test_successful_fallback_does_not_enter_cooldown_and_metrics_are_secret_free(
        self,
    ):
        secret = "private-prompt-must-not-appear"
        body = self._body(
            {
                "stream": False,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": secret}],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            port, _received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        key = policy.request_fingerprint(body)
        assert self._cooldown_remaining(key) == 0
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_attempts"] == 1
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_accepted"] == 1
        assert secret not in json.dumps(status)
        assert key not in json.dumps(status)

    def test_dedicated_slot_survives_ordinary_transient_retry_budget(self):
        body = self._body(
            {
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        transient = b'{"error":{"code":"upstream_failure"}}'
        responses = [
            (500, transient),
            (500, transient),
            (500, transient),
            (477, EMPTY_RESPONSE),
            (200, SUCCESS),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy(responses, tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        assert len(received) == 5
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_attempts"] == 1
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_accepted"] == 1

    def test_fallback_follows_response_failed_recovery_without_metric_overlap(self):
        error = b'{"error":{"type":"new_api_error","code":"response_failed"}}'
        body = self._body(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "x" * 100_000},
                    {"type": "message", "role": "user", "content": "latest"},
                ]
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy(
                [(400, error), (477, EMPTY_RESPONSE), (200, SUCCESS)], tmp
            )
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        counters = cast("dict[str, int]", proxy.runtime_status()["counters"])
        assert len(received) == 3
        assert len(received[1]) < len(received[0])
        assert received[2] == received[1]
        assert counters["response_failed_compaction_attempts"] == 1
        assert counters["response_failed_compaction_accepted"] == 0
        assert counters["wire_failure_retry_attempts"] == 1
        assert counters["wire_failure_retry_accepted"] == 1

    def test_downstream_disconnect_releases_response_slot(self):
        body = self._body(
            {
                "previous_response_id": "stale",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        started, release = threading.Event(), threading.Event()
        scripted = {
            "status": 200,
            "chunks": [b'data: {"type":"response.completed"}\n\n'],
            "started_event": started,
            "release_event": release,
        }
        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), scripted], tmp)
            try:
                sock = socket.create_connection(("127.0.0.1", port))
                sock.sendall(
                    b"\r\n".join(
                        (
                            b"POST /dmxapi/v1/responses HTTP/1.1",
                            f"Host: 127.0.0.1:{port}".encode(),
                            f"Content-Length: {len(body)}".encode(),
                            b"Connection: close",
                            b"",
                            body,
                        )
                    )
                )
                assert started.wait(timeout=10)
                with contextlib.suppress(OSError):
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0),
                    )
                sock.close()
                release.set()
                for _ in range(50):
                    active = proxy.runtime_status()["active_responses"]
                    if not active:
                        break
                    time.sleep(0.1)
            finally:
                cleanup()

        assert len(received) == 2
        assert proxy.runtime_status()["active_responses"] == 0
        assert self._cooldown_remaining(policy.request_fingerprint(body)) == 0
