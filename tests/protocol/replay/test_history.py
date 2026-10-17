"""Provider-bound history projection contracts."""

from __future__ import annotations

import json

import pytest

from openai_responses_proxy.protocol.replay import content as portable_content
from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import HISTORY_PAYLOAD
from tests.protocol.replay.fixtures import body as _body


class ProviderPortableHistoryTests:
    """The normal outbound path owns provider-portable history."""

    @pytest.mark.parametrize("root_ciphertext", [False, True])
    def test_preserves_encrypted_agent_payload_with_its_native_envelope(self, root_ciphertext):
        message = {
            "type": "agent_message",
            "id": "agent_delivery",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": [{"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"}],
            "internal_chat_message_metadata_passthrough": {"opaque": True},
        }
        if root_ciphertext:
            message["encrypted_content"] = "fixture-ciphertext"
        else:
            message["content"].append(
                {"type": "encrypted_content", "encrypted_content": "fixture-ciphertext"}
            )
        result = rewrite.sanitize_responses_body(_body({"input": [message]}))

        assert result.body is not None, result.diagnostic()
        expected = {
            k: v for k, v in message.items() if k != "internal_chat_message_metadata_passthrough"
        }
        assert json.loads(result.body)["input"] == [expected]
        assert result.metrics.encrypted_blocks == 0
        assert result.metrics.omission_markers == 0
        assert rewrite.sanitize_responses_body(result.body).body == result.body

    @pytest.mark.parametrize("ciphertext", [None, "", 1, {"nested": "value"}])
    def test_rejects_invalid_encrypted_agent_payload_without_empty_task(self, ciphertext):
        message = {
            "type": "agent_message",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": [
                {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                {"type": "encrypted_content", "encrypted_content": ciphertext},
            ],
        }
        result = rewrite.sanitize_responses_body(_body({"input": [message]}))

        assert result.body is None
        assert result.reason == "invalid_encrypted_content"

    @pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
    def test_preserves_named_async_deliveries_at_their_original_positions(self, call_type):
        argument = "arguments" if call_type == "function_call" else "input"
        call = {"type": call_type, "call_id": "job", "name": "run", argument: "{}"}
        initial = {
            "type": f"{call_type}_output",
            "call_id": "job",
            "id": "initial",
            "output": "Still running\n",
        }
        followup = {"type": "message", "role": "user", "content": "Continue independently"}
        deliveries = [
            {**initial, "id": "progress", "name": "run", "output": "First result\n"},
            {**initial, "id": "final", "name": "run", "output": "Complete\n"},
        ]
        raw = _body({"input": [call, initial, followup, *deliveries]})

        result = rewrite.sanitize_responses_body(raw)

        assert result.body is not None, result.diagnostic()
        items = json.loads(result.body)["input"]
        assert items[:3] == [call, {k: v for k, v in initial.items() if k != "id"}, followup]
        for item, delivery in zip(items[3:], deliveries, strict=True):
            assert item["type"] == "message"
            assert item["role"] == "assistant"
            header, output = item["content"].split("\n", 1)
            assert json.loads(header) == {
                "type": "tool_delivery",
                "call_id": "job",
                "name": "run",
            }
            assert output == delivery["output"]
        assert result.input_dependencies.edges == [(1, 0), (3, 0), (4, 0)]
        assert not result.input_dependencies.allows({2, 3, 4})
        assert rewrite.sanitize_responses_body(result.body).body == result.body

    def test_drops_empty_assistant_placeholders_from_replay(self) -> None:
        raw = _body(
            {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "message",
                        "id": "msg_empty",
                        "role": "assistant",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": ""}],
                        "internal_chat_message_metadata_passthrough": {"opaque": True},
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "continue"}],
                    },
                ],
            }
        )

        projection = rewrite.sanitize_responses_body(raw)

        assert projection.status == "projected", projection.diagnostic()
        assert projection.body is not None
        assert json.loads(projection.body)["input"] == [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue"}],
            }
        ]
        assert projection.metrics is not None
        assert projection.metrics.changed_items == 1

    def test_projects_standalone_namespaced_tool_delivery_to_message(self) -> None:
        raw = _body(
            {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "function_call_output",
                        "id": "fco_delivery",
                        "name": "send_message_to_thread",
                        "namespace": "codex_app",
                        "output": "<codex_delegation>delivered</codex_delegation>",
                        "internal_chat_message_metadata_passthrough": {"opaque": True},
                    }
                ],
            }
        )

        projection = rewrite.sanitize_responses_body(raw)

        assert projection.status == "projected", projection.diagnostic()
        assert projection.body is not None
        item = json.loads(projection.body)["input"][0]
        assert item == {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": '{"type":"tool_delivery","name":"send_message_to_thread","namespace":"codex_app"}\n'
            "<codex_delegation>delivered</codex_delegation>",
        }

    def test_rejects_incomplete_or_hybrid_standalone_tool_delivery(self) -> None:
        invalid_items = (
            {
                "type": "function_call_output",
                "id": "fco_delivery",
                "namespace": "codex_app",
                "output": "delivered",
            },
            {
                "type": "function_call_output",
                "id": "fco_delivery",
                "name": "send_message_to_thread",
                "namespace": "codex_app",
                "call_id": "call_missing",
                "output": "delivered",
            },
        )

        for item in invalid_items:
            projection = rewrite.sanitize_responses_body(
                _body({"model": "gpt-test", "input": [item]})
            )
            assert projection.status == "rejected"

    def test_projects_provider_bound_history_to_closed_portable_grammar(self) -> None:
        raw = _body(HISTORY_PAYLOAD)

        _projection = rewrite.sanitize_responses_body(raw)
        projected_raw = _projection.body
        note = _projection.diagnostic()

        assert projected_raw is not None, note
        projected = json.loads(projected_raw)
        for field in ("previous_response_id", "conversation", "prompt_cache_key"):
            assert field not in projected
        assert projected["include"] == ["other"]
        assert projected["store"] is False
        assert [item["type"] for item in projected["input"]] == [
            "message",
            "agent_message",
            "function_call",
            "function_call_output",
            "custom_tool_call",
            "custom_tool_call_output",
            "message",
        ]
        assistant = projected["input"][0]
        assert assistant["phase"] == "final_answer"
        assert assistant["content"] == "visible answer"
        agent = projected["input"][1]
        original_agent = next(
            item for item in json.loads(raw)["input"] if item["type"] == "agent_message"
        )
        assert agent == {
            key: value
            for key, value in original_agent.items()
            if key != "internal_chat_message_metadata_passthrough"
        }
        function_call, function_output = projected["input"][2:4]
        assert function_call["call_id"] == function_output["call_id"]
        assert function_output["output"] == [
            {"type": "input_text", "text": "visible function output"}
        ]
        custom_call, custom_output = projected["input"][4:6]
        assert custom_call["call_id"] == custom_output["call_id"]
        assert custom_output["output"] == [
            {"type": "input_text", "text": portable_content.OPAQUE_CONTENT_MARKER}
        ]
        serialized = projected_raw.decode()
        for forbidden in (
            "rs_provider_bound",
            "rs_stored_reference",
            "opaque-reasoning",
            "tool-secret",
            "only-secret",
            "internal_chat_message_metadata_passthrough",
        ):
            assert forbidden not in serialized
        assert "provider_bindings=3" in note
        assert "reasoning_items=1" in note
