"""Provider-portable request projection contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body


def _request_body(input_items: object, **extra: object) -> bytes:
    return _body({"input": input_items, **extra})


class RequestSanitizationContracts:
    def test_strips_reasoning_and_encrypted(self):
        body = _request_body(
            [
                {"type": "reasoning", "encrypted_content": "gAAAA_secret"},
                {"type": "message", "role": "user", "content": "hello"},
            ],
            include=["reasoning.encrypted_content", "other"],
        )
        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        _ = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))
        assert obj["input"] == [{"type": "message", "role": "user", "content": "hello"}]
        assert "reasoning.encrypted_content" not in obj["include"]

    def test_preserves_native_agent_message_and_removes_reasoning(self):
        body = json.dumps(
            {
                "input": [
                    {"type": "reasoning", "encrypted_content": "gAAAA_replay_only"},
                    {
                        "type": "agent_message",
                        "author": "agent",
                        "recipient": "user",
                        "content": [
                            {"type": "input_text", "text": "reply"},
                            {
                                "type": "encrypted_content",
                                "encrypted_content": "required_agent_message_payload",
                            },
                        ],
                    },
                ],
                "include": ["reasoning.encrypted_content", "other"],
            }
        ).encode()
        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))
        assert len(obj["input"]) == 1
        agent = obj["input"][0]
        assert agent == json.loads(body)["input"][1]
        assert "required_agent_message_payload" in cast("bytes", out).decode()
        assert "encrypted_blocks=0" in note
        assert "reasoning_items=1" in note
        assert "reasoning.encrypted_content" not in obj["include"]

    def test_rejects_malformed_agent_ciphertext_instead_of_partial_delivery(self):
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "agent_message",
                        "author": "agent",
                        "recipient": "user",
                        "content": [
                            {"type": "input_text", "text": "before"},
                            {"type": "encrypted_content"},
                            {
                                "type": "encrypted_content",
                                "encrypted_content": "valid_required_payload",
                            },
                            {"type": "input_text", "text": "after"},
                        ],
                    }
                ]
            }
        ).encode()
        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        assert out is None
        assert note == "rejected invalid_encrypted_content"

    def test_rejects_unknown_fields_that_resemble_encrypted_content(self):
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "custom_tool_call",
                        "payload": {"type": "encrypted_content"},
                    }
                ]
            }
        ).encode()
        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        assert out is None
        assert note == "rejected unknown_call_field"

    def test_fail_closed_on_non_json(self):
        raw = b"not json at all"
        _projection = rewrite.sanitize_responses_body(raw)
        out = _projection.body
        note = _projection.diagnostic()
        assert out is None
        assert note == "rejected invalid_json"

    def test_fail_closed_on_json_values_that_are_not_response_objects(self, subtests):
        for raw in (b"[]", b"null", b'"text"'):
            with subtests.test(raw=raw):
                _projection = rewrite.sanitize_responses_body(raw)
                out = _projection.body
                note = _projection.diagnostic()
                assert out is None
                assert note == "rejected request_not_object"

    def test_request_sanitizer_fails_closed_when_mutation_cannot_be_serialized(self, *, mocker):
        raw = b'{"input":[{"type":"reasoning","encrypted_content":"opaque"},{"type":"message","role":"user","content":"continue"}]}'
        serializer = mocker.Mock(side_effect=TypeError("unsupported"))
        mocker.patch.object(
            rewrite,
            "json",
            SimpleNamespace(
                loads=json.loads, dumps=serializer, JSONDecodeError=json.JSONDecodeError
            ),
        )
        _projection = rewrite.sanitize_responses_body(raw)
        out = _projection.body
        note = _projection.diagnostic()
        assert out is None
        assert note == "rejected serialization_failed"
        serializer.assert_called_once()

    def test_deep_request_fails_closed(self) -> None:
        nested = '{"x":' * 496 + "0" + "}" * 496
        raw = ('{"input":[{"type":"message","role":"user","content":' + nested + "}]}").encode()
        projection = rewrite.sanitize_responses_body(raw)
        assert projection.body is None
        assert projection.diagnostic().startswith("rejected ")
