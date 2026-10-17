"""Provider-portable request projection contracts."""

from __future__ import annotations

import json

from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body


class TestPortableDialogueProjection:
    def test_reports_recognized_unimplemented_standard_item_as_schema_drift(
        self,
    ) -> None:
        projection = rewrite.sanitize_responses_body(_body({"input": [{"type": "shell_call"}]}))

        assert projection.body is None
        assert projection.diagnostic() == "rejected schema_drift"

    def test_normalizes_typed_dialogue_content_by_projected_role(self) -> None:
        raw = _body(
            {
                "input": [
                    {
                        "type": "message",
                        "role": "system",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "system",
                                "annotations": [],
                            }
                        ],
                    },
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "output_text", "text": "developer", "logprobs": []}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "user",
                                "prompt_cache_breakpoint": {"mode": "explicit"},
                            }
                        ],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "answer",
                                "prompt_cache_breakpoint": {"mode": "explicit"},
                            },
                            {"type": "refusal", "refusal": "declined"},
                        ],
                    },
                ]
            }
        )

        _projection = rewrite.sanitize_responses_body(raw)
        projected_raw = _projection.body
        note = _projection.diagnostic()

        assert projected_raw is not None, note
        content = [item["content"] for item in json.loads(projected_raw)["input"]]
        assert content == [
            [{"type": "input_text", "text": "system"}],
            [{"type": "input_text", "text": "developer"}],
            [{"type": "input_text", "text": "user"}],
            "answerdeclined",
        ]

    def test_fails_closed_for_unknown_or_malformed_replay_shapes(self, subtests) -> None:
        cases = {
            "invalid json": b"{",
            "non object": b"[]",
            "invalid input": _body({"input": {"type": "message"}}),
            "unknown item": _body({"input": [{"type": "future_item", "opaque": True}]}),
            "unknown block": _body(
                {
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "future_content", "value": "opaque"}],
                        }
                    ]
                }
            ),
            "orphan output": _body(
                {
                    "input": [
                        {
                            "type": "function_call_output",
                            "call_id": "missing",
                            "output": "x",
                        }
                    ]
                }
            ),
            "mismatched output": _body(
                {
                    "input": [
                        {
                            "type": "function_call",
                            "call_id": "c",
                            "name": "f",
                            "arguments": "{}",
                        },
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "c",
                            "output": "x",
                        },
                    ]
                }
            ),
        }
        for name, raw in cases.items():
            with subtests.test(name=name):
                _projection = rewrite.sanitize_responses_body(raw)
                projected = _projection.body
                note = _projection.diagnostic()
                assert projected is None
                assert note.startswith("rejected "), note
