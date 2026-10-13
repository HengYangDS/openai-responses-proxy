"""Generation-time plaintext delegation and exact delivery identity contracts."""

from __future__ import annotations

import json

import pytest

from openai_responses_proxy.protocol import agent_delivery
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body


def _wire(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode()


def _tools() -> list[dict[str, object]]:
    return [
        {
            "type": "namespace",
            "name": "collaboration",
            "tools": [
                {
                    "type": "function",
                    "name": name,
                    "parameters": {
                        "type": "object",
                        "properties": {"message": {"type": "string", "encrypted": True}},
                    },
                }
                for name in ("spawn_agent", "send_message", "followup_task", "wait_agent")
            ],
        }
    ]


def _call(message: str = "Exact message: 青石\n37; Q7k9") -> dict[str, object]:
    return {
        "type": "function_call",
        "id": "fc-1",
        "call_id": "call-1",
        "name": "send_message",
        "namespace": agent_delivery.PLAINTEXT_NAMESPACE,
        "arguments": json.dumps({"target": "/root", "message": message}, ensure_ascii=False),
    }


class AgentDeliveryContracts:
    def test_plaintext_schema_selection_covers_live_and_replayed_tool_catalogs(self) -> None:
        original = {
            "tools": _tools(),
            "input": [{"type": "additional_tools", "tools": _tools()}],
        }
        request = agent_delivery.prepare_request(_wire(original), "plaintext")
        assert request.active
        projected = json.loads(request.body)
        for catalog in (projected["tools"], projected["input"][0]["tools"]):
            assert catalog[0]["name"] == agent_delivery.PLAINTEXT_NAMESPACE
            for tool in catalog[0]["tools"]:
                message = tool["parameters"]["properties"]["message"]
                assert message.get("encrypted") is (True if tool["name"] == "wait_agent" else None)
        assert original["tools"][0]["name"] == "collaboration"

    def test_remaps_replayed_calls_without_touching_arguments_or_agent_ciphertext(self) -> None:
        call = _call()
        call["namespace"] = "collaboration"
        call["encrypted_function_args"] = []
        encrypted = {
            "type": "agent_message",
            "author": "/root/child",
            "recipient": "/root",
            "content": [{"type": "encrypted_content", "encrypted_content": "gAAAAOldOpaque"}],
        }
        raw = _wire({"input": [call, encrypted]})
        prepared = agent_delivery.prepare_request(raw, "plaintext")
        data = json.loads(prepared.body)
        assert prepared.active
        assert data["input"][0]["namespace"] == agent_delivery.PLAINTEXT_NAMESPACE
        assert "encrypted_function_args" not in data["input"][0]
        assert data["input"][0]["arguments"] == call["arguments"]
        assert data["input"][1] == encrypted

    def test_native_and_unrelated_requests_keep_exact_wire_bytes(self) -> None:
        raw = b'{ "input": [], "tools": [] }'
        assert agent_delivery.prepare_request(raw, "native").body == raw
        assert agent_delivery.prepare_request(raw, "plaintext").body == raw
        other = _tools()
        other[0]["name"] = "another_tool_namespace"
        raw = _wire({"tools": other, "input": []})
        assert agent_delivery.prepare_request(raw, "plaintext").body == raw

    def test_reserved_alias_collision_rejects_before_upstream_io(self) -> None:
        tools = _tools()
        tools.append({"type": "namespace", "name": agent_delivery.PLAINTEXT_NAMESPACE, "tools": []})
        with pytest.raises(ValueError, match="agent_delivery_namespace_conflict"):
            agent_delivery.prepare_request(_wire({"tools": tools, "input": []}), "plaintext")

    def test_selected_tool_choice_follows_the_declared_namespace(self) -> None:
        choice = {"type": "function", "name": "send_message", "namespace": "collaboration"}
        prepared = agent_delivery.prepare_request(
            _wire({"tools": _tools(), "input": [], "tool_choice": choice}), "plaintext"
        )
        assert json.loads(prepared.body)["tool_choice"] == {
            **choice,
            "namespace": agent_delivery.PLAINTEXT_NAMESPACE,
        }

    def test_incomplete_message_schema_cannot_select_plaintext(self) -> None:
        tools = json.loads(_wire(_tools()))
        tools[0]["tools"][0]["parameters"]["properties"]["message"] = {"type": "object"}
        with pytest.raises(ValueError, match="invalid_agent_delivery_request"):
            agent_delivery.prepare_request(_wire({"tools": tools}), "plaintext")

    @pytest.mark.parametrize("metadata", [None, "message", [1]])
    def test_replay_metadata_must_match_the_native_argument_list(self, metadata: object) -> None:
        call = {**_call(), "namespace": "collaboration", "encrypted_function_args": metadata}
        projected = sanitize_responses_body(
            _wire(
                {
                    "input": [
                        call,
                        {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
                    ]
                }
            )
        )
        assert projected.body is None
        assert projected.reason == "invalid_encrypted_function_args"

    @pytest.mark.parametrize("raw", [b"[1]", b"invalid", b'{"input":[],"tools":{}}'])
    def test_rejects_malformed_requests_without_partial_transformation(self, raw: bytes) -> None:
        with pytest.raises(ValueError, match="invalid_agent_delivery_request"):
            agent_delivery.prepare_request(raw, "plaintext")

    def test_stream_and_terminal_calls_enter_native_plaintext_delivery_with_exact_body(
        self,
    ) -> None:
        call = _call()
        arguments = call["arguments"]
        for event_type in ("response.output_item.added", "response.output_item.done"):
            wire = (
                b"event: "
                + event_type.encode()
                + b"\r\ndata: "
                + _wire({"type": event_type, "item": call})
                + b"\r\n\r\n"
            )
            result = agent_delivery.restore_event(wire, True)
            item = json.loads(result.split(b"data: ", 1)[1])["item"]
            assert item["namespace"] == "collaboration"
            assert item["encrypted_function_args"] == []
            assert item["arguments"] == arguments
            assert item["call_id"] == "call-1"
        terminal = agent_delivery.restore_response(
            _wire({"status": "completed", "output": [call]}), True
        )
        restored = json.loads(terminal)["output"][0]
        assert restored["arguments"] == arguments
        assert restored["encrypted_function_args"] == []

    @pytest.mark.parametrize("name", ["spawn_agent", "send_message", "followup_task"])
    def test_every_message_tool_uses_the_same_plaintext_contract(self, name: str) -> None:
        call = _call()
        call["name"] = name
        result = json.loads(agent_delivery.restore_response(_wire({"output": [call]}), True))
        assert result["output"][0]["encrypted_function_args"] == []

    def test_refuses_ciphertext_as_plaintext_and_never_relabels_existing_encrypted_args(
        self,
    ) -> None:
        for call in (
            _call("gAAAAOpaqueCiphertext"),
            {**_call(), "encrypted_function_args": ["message"]},
        ):
            with pytest.raises(ValueError, match="agent_delivery_plaintext_unproved"):
                agent_delivery.restore_response(_wire({"output": [call]}), True)

    def test_unrelated_response_content_and_native_policy_remain_exact(self) -> None:
        raw = _wire(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": agent_delivery.PLAINTEXT_NAMESPACE}
                        ],
                    }
                ]
            }
        )
        assert agent_delivery.restore_response(raw, True) == raw
        assert agent_delivery.restore_response(_wire({"output": [_call()]}), False) == _wire(
            {"output": [_call()]}
        )
        for event in (b"event: ping\n\n", b"data: [DONE]\n\n"):
            assert agent_delivery.restore_event(event, True) == event

    def test_streaming_call_start_can_precede_argument_generation(self) -> None:
        call = {**_call(), "status": "in_progress", "arguments": ""}
        raw = _wire({"type": "response.output_item.added", "item": call})
        event = agent_delivery.restore_event(b"data: " + raw + b"\n\n", True)
        restored = json.loads(event.split(b"data: ", 1)[1])["item"]
        assert restored["namespace"] == "collaboration"
        assert restored["arguments"] == ""
        assert "encrypted_function_args" not in restored

    @pytest.mark.parametrize("event_type", ["response.created", "response.in_progress"])
    def test_nonterminal_response_snapshot_can_precede_argument_generation(
        self, event_type: str
    ) -> None:
        call = {**_call(), "status": "in_progress", "arguments": ""}
        response = {"status": "in_progress", "output": [call]}
        raw = b"data: " + _wire({"type": event_type, "response": response}) + b"\n\n"
        restored = json.loads(agent_delivery.restore_event(raw, True).split(b"data: ", 1)[1])
        item = restored["response"]["output"][0]
        assert item["namespace"] == "collaboration"
        assert item["arguments"] == ""
        assert "encrypted_function_args" not in item

    @pytest.mark.parametrize(
        ("context", "event_type"),
        [
            ("json", None),
            ("json", "response.in_progress"),
            ("sse", "response.output_item.done"),
            ("sse", "response.completed"),
            ("sse", "response.incomplete"),
        ],
    )
    def test_terminal_context_requires_readable_arguments_despite_in_progress_item(
        self, context: str, event_type: str | None
    ) -> None:
        call = {**_call(), "status": "in_progress", "arguments": ""}
        terminal = {"status": "completed", "output": [call]}
        if context == "json":
            if event_type is not None:
                terminal["type"] = event_type
            raw = _wire(terminal)
            restore = agent_delivery.restore_response
        else:
            payload = (
                {"type": event_type, "item": call}
                if event_type == "response.output_item.done"
                else {"type": event_type, "response": terminal}
            )
            raw = b"data: " + _wire(payload) + b"\n\n"
            restore = agent_delivery.restore_event
        with pytest.raises(ValueError, match="agent_delivery_plaintext_unproved"):
            restore(raw, True)

    def test_replayed_native_plaintext_marker_has_a_declared_item_grammar(self) -> None:
        call = _call()
        call["namespace"] = "collaboration"
        call["encrypted_function_args"] = []
        raw = _wire(
            {
                "store": False,
                "input": [
                    call,
                    {"type": "function_call_output", "call_id": "call-1", "output": "delivered"},
                ],
            }
        )
        projected = sanitize_responses_body(raw)
        assert projected.body is not None, projected.reason
        assert json.loads(projected.body)["input"][0]["encrypted_function_args"] == []
        replay = agent_delivery.prepare_request(projected.body, "plaintext")
        assert json.loads(replay.body)["input"][0]["arguments"] == call["arguments"]
