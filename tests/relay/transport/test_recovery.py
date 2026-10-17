"""HTTP recovery, attempt budgets and telemetry through real loopback hops."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import LoopbackTransportFixture
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy


class TestProxyTransport(LoopbackTransportFixture):
    def test_recovers_response_failed_with_pair_safe_compact_request(self):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_old",
                        "name": "tool",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_old",
                        "output": "old result",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        received, payload = self.exchange([(400, response_failed), (200, success)], body)
        assert payload == success

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 2
        compact = json.loads(received[1])
        assert len(received[1]) < len(body)
        assert "prompt_cache_key" not in compact
        assert compact["input"][-1]["content"] == "latest user context"
        call_types = {"custom_tool_call", "function_call"}
        output_types = {"custom_tool_call_output", "function_call_output"}
        calls = {item["call_id"] for item in compact["input"] if item.get("type") in call_types}
        outputs = {item["call_id"] for item in compact["input"] if item.get("type") in output_types}
        assert outputs.issubset(calls)

    def test_recovers_blocked_invalid_prompt_with_pair_safe_compact_request(self):
        blocked = (
            b'{"error":{"message":"Request blocked. (request id: fixture)",'
            b'"type":"invalid_request_error","param":"","code":"invalid_prompt"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {"type": "message", "role": "developer", "content": "policy"},
                    {
                        "type": "message",
                        "role": "user",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_old",
                        "name": "tool",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_old",
                        "output": "old result",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        received, payload = self.exchange([(400, blocked), (200, success)], body)
        assert payload == success

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 2
        compact = json.loads(received[1])
        assert len(received[1]) < len(body)
        assert "prompt_cache_key" not in compact
        assert compact["input"][-1]["content"] == "latest user context"
        classifications = cast("dict[str, int]", proxy.runtime_status()["upstream_classifications"])
        assert classifications.get("blocked_invalid_prompt") == 1
        assert "response_failed" not in classifications

    def test_passes_through_unrelated_invalid_prompt(self):
        invalid_prompt = (
            b'{"error":{"message":"caller supplied an unsupported option",'
            b'"type":"invalid_request_error","param":"tool_choice","code":"invalid_prompt"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        with running_proxy([(400, invalid_prompt)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value:
                assert raised.value.code == 400
                assert raised.value.read() == invalid_prompt

        assert json.loads(received[0]) == {**json.loads(body), "store": False}

    def test_rejected_encrypted_delegation_keeps_its_required_task_payload(self):
        rejection = b'{"error":{"type":"invalid_request_error","code":"invalid_encrypted_content"}}'
        request_body = json.dumps(
            {
                "model": "gpt-6-astra",
                "stream": False,
                "input": [
                    {
                        "type": "agent_message",
                        "author": "/root",
                        "recipient": "/root/reviewer",
                        "content": [
                            {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                            {"type": "encrypted_content", "encrypted_content": "task-payload"},
                        ],
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()

        with running_proxy(
            [(400, rejection), (200, b'{"id":"resp_unrelated","status":"completed","output":[]}')]
        ) as (
            port,
            received,
        ):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, request_body)
            with raised.value:
                assert raised.value.code == 400
                assert raised.value.read() == rejection

        assert len(received) == 1
        assert b"task-payload" in received[0]
        snapshot = telemetry.status(
            release="test",
            serving_payload_sha256=None,
            release_receipt_sha256=None,
            admission={},
            runtime_identity={},
        )
        classifications = cast("dict[str, int]", snapshot["upstream_classifications"])
        assert classifications["provider_bound_encrypted_content"] == 1

    def test_response_failed_final_recovery_retains_the_current_tool_pair(self, *, mocker):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current policy",
                    },
                    {
                        "type": "message",
                        "role": "system",
                        "content": "old system policy" * 1000,
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                    {
                        "type": "message",
                        "role": "system",
                        "content": "current system policy",
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_new",
                        "name": "tool",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_new",
                        "output": "tool result",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 1)
        log = mocker.patch.object(operational_log, "log")

        with (
            running_proxy([(400, response_failed), (400, response_failed), (200, success)]) as (
                port,
                received,
            ),
            request(port, body) as response,
        ):
            assert response.status == 200
            assert response.read() == success
        logs = "\n".join(call.args[0] for call in log.call_args_list)

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 3
        recovery = json.loads(received[2])
        assert "prompt_cache_key" not in recovery
        assert recovery["input"] == [
            {"type": "message", "role": "developer", "content": "current policy"},
            {"type": "message", "role": "user", "content": "latest user context"},
            {"type": "message", "role": "system", "content": "current system policy"},
            {"type": "custom_tool_call", "call_id": "call_new", "name": "tool", "input": "{}"},
            {"type": "custom_tool_call_output", "call_id": "call_new", "output": "tool result"},
        ]
        assert "event=response_failed_dialogue_recovery_accepted" in logs
        assert "event=response_failed_compact_recovery_accepted" not in logs

    def test_normalizes_exhausted_response_failed_recovery_to_retryable_503(self, *, mocker):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current policy",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_new",
                        "name": "tool",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_new",
                        "output": "tool result",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 0)

        with running_proxy([(400, response_failed), (400, response_failed)]) as (
            port,
            received,
        ):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert len(received) == 1
        assert payload["error"]["code"] == "response_failed_recovery_exhausted"

    def test_retries_classified_empty_response_at_most_once_with_unchanged_body(self, *, mocker):
        empty_response = (
            b'{"error":{"message":"official provider returned an empty response",'
            b'"type":"dmx_api_error","code":"empty_response"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        received, payload = self.exchange([(477, empty_response), (200, success)], body)
        assert payload == success

        assert received[0] == received[1]
        assert json.loads(received[0]) == {**json.loads(body), "store": False}

    def test_runtime_metrics_classify_recovery_without_retaining_request_content(self):
        response_failed = b'{"error":{"type":"new_api_error","code":"response_failed"}}'
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "stream": False,
                "input": [
                    {"type": "reasoning", "encrypted_content": "secret-replay"},
                    {"type": "message", "role": "user", "content": "old context"},
                    {"type": "message", "role": "user", "content": "x" * 100_000},
                    {"type": "message", "role": "user", "content": "private prompt"},
                ],
            },
            separators=(",", ":"),
        ).encode()

        _received, payload = self.exchange([(400, response_failed), (200, success)], body)
        assert payload == success

        status = proxy.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        classifications = cast("dict[str, int]", status["upstream_classifications"])
        assert counters["responses_received"] == 1
        assert counters["encrypted_replayed_reasoning_items_stripped"] == 1
        assert counters["response_failed_compaction_attempts"] == 1
        assert counters["response_failed_compaction_accepted"] == 1
        assert classifications["response_failed"] == 1
        assert "private prompt" not in json.dumps(status)
        assert "secret-replay" not in json.dumps(status)

    def test_normalizes_exhausted_classified_empty_response_to_retryable_503(self, *, mocker):
        empty_response = (
            b'{"error":{"message":"official provider returned an empty response",'
            b'"type":"dmx_api_error","code":"empty_response"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)

        with running_proxy([(477, empty_response)] * 4) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert received[0] == received[1]
        assert json.loads(received[0]) == {**json.loads(body), "store": False}
        assert payload["error"]["type"] == "upstream_unavailable"
        assert payload["error"]["code"] == "dmx_empty_response_exhausted"
        assert payload["error"]["attempts"] == 2
