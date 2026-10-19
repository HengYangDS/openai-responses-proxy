"""Portable replay admission before classified empty-response recovery."""

from __future__ import annotations

import json
import tempfile

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from tests.relay.empty_response.fixtures import EMPTY_RESPONSE
from tests.relay.empty_response.fixtures import SUCCESS
from tests.relay.empty_response.fixtures import EmptyResponseFixture
from tests.relay.empty_response.fixtures import semantic_body
from tests.relay.fixtures import serve_proxy


class EmptyResponseTransportTests(EmptyResponseFixture):
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
                    {"type": "tool_search_output", "call_id": "search", "execution": "server"},
                    {"type": "message", "role": "user", "content": "current"},
                ],
                "output_before_call",
            ),
            (
                [
                    {"type": "tool_search_call", "call_id": "search", "execution": "server"},
                    {"type": "tool_search_call", "call_id": "search", "execution": "server"},
                    {"type": "message", "role": "user", "content": "current"},
                ],
                "duplicate_call",
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
