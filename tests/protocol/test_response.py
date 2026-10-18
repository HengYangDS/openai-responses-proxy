"""Live Responses validation contracts."""

from __future__ import annotations

import json

import pytest

from openai_responses_proxy.protocol import response


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _event(value: object) -> bytes:
    return b"event: response.output_item.added\ndata: " + _json(value) + b"\n\n"


class LiveStreamContracts:
    @pytest.mark.parametrize("event", [b'data:{"type":\n\n', b'data: {"type":\ndata: bad}\n\n'])
    def test_validates_all_sse_data_field_spellings(self, event: bytes) -> None:
        with pytest.raises(ValueError, match="invalid_responses_event"):
            response.validate_sse_event(event)

    def test_multiline_sse_data_is_one_json_document(self) -> None:
        event = b'data: {"type":\ndata: "response.created"}\n\n'
        assert response.validate_sse_event(event) == event

    def test_preserves_encrypted_agent_payload_for_current_turn_decryption(
        self,
    ) -> None:
        event = _event(
            {
                "item": {
                    "type": "agent_message",
                    "content": [
                        {
                            "type": "encrypted_content",
                            "encrypted_content": "agent-secret",
                        }
                    ],
                }
            }
        )

        assert response.validate_sse_event(event) == event

    def test_preserves_encrypted_tool_payload_for_current_turn_decryption(self) -> None:
        event = _event(
            {
                "item": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": [],
                    "encrypted_content": "tool-secret",
                }
            }
        )

        assert response.validate_sse_event(event) == event

    def test_passes_non_json_data_and_events_without_data_unchanged(self, subtests) -> None:
        for event in (b"data: [DONE]\n\n", b"event: ping\n\n"):
            with subtests.test(event=event):
                assert response.validate_sse_event(event) == event

    def test_rejects_malformed_json_data_atomically(self) -> None:
        with pytest.raises(ValueError, match="invalid_responses_event"):
            response.validate_sse_event(b'data: {"type":\n\n')


class LocalErrorContracts:
    def test_error_envelope_has_one_canonical_wire_encoding(self) -> None:
        assert response.error_payload("retry", "upstream_unavailable", "failed") == (
            b'{"error":{"message":"retry","type":"upstream_unavailable","code":"failed"}}'
        )
        assert response.error_payload(
            "retry",
            "upstream_unavailable",
            "failed",
            reason="unsafe_shape",
            attempts=2,
        ) == (
            b'{"error":{"message":"retry","type":"upstream_unavailable",'
            b'"code":"failed","reason":"unsafe_shape","attempts":2}}'
        )


class LiveJsonContracts:
    def test_preserves_terminal_ciphertext_for_current_turn_decryption(self) -> None:
        payload = _json(
            {
                "id": "response-1",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "encrypted_content": "reasoning-secret"},
                    {
                        "type": "agent_message",
                        "content": [
                            {
                                "type": "encrypted_content",
                                "encrypted_content": "agent-secret",
                            }
                        ],
                    },
                ],
            }
        )

        assert response.validate_json_response(payload) == payload

    def test_accepts_both_terminal_success_states(self, subtests) -> None:
        for status in ("completed", "incomplete"):
            payload = _json({"status": status, "output": []})
            with subtests.test(status=status):
                assert response.validate_json_response(payload) == payload

    @pytest.mark.parametrize("status", [[], {}])
    def test_nonstring_terminal_status_has_a_bounded_rejection(self, status: object) -> None:
        with pytest.raises(ValueError, match=r"^invalid_responses_success_body$"):
            response.validate_json_response(_json({"status": status, "output": []}))

    def test_rejects_unproved_success_documents(self, subtests) -> None:
        invalid = (
            b"",
            b"not-json",
            _json([]),
            _json({"output": []}),
            _json({"status": "in_progress", "output": []}),
            _json({"status": "failed", "output": []}),
        )
        for payload in invalid:
            with (
                subtests.test(payload=payload),
                pytest.raises(ValueError, match="invalid_responses_success_body"),
            ):
                response.validate_json_response(payload)
