"""Provider-classified empty-response payloads and transport mechanics."""

from __future__ import annotations

import json
import urllib.error

import pytest

from openai_responses_proxy.providers.policies import dmxapi as policy
from openai_responses_proxy.relay import cooldown
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import request

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


class EmptyResponseFixture(InputTransportFixture):
    """Share exact empty-response cooldown and HTTP-error mechanics."""

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
