"""Provider-portable grammar rejection contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body

if TYPE_CHECKING:
    import pytest

REJECTION_CASES = {
    "empty input": ({"input": ""}, "rejected empty_input"),
    "invalid include": ({"input": "hello", "include": [1]}, "rejected invalid_include"),
    "invalid item": ({"input": ["not-an-item"]}, "rejected invalid_item"),
    "only provider-bound items": (
        {"input": [{"type": "reasoning"}]},
        "rejected empty_portable_input",
    ),
    "empty message text": (
        {"input": [{"type": "message", "role": "user", "content": ""}]},
        "rejected empty_text_content",
    ),
    "invalid content block": (
        {"input": [{"type": "message", "role": "user", "content": [1]}]},
        "rejected invalid_content_block",
    ),
    "invalid text block": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": 1}],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "input text has unknown metadata": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "x", "unknown": True}],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "input text has invalid cache breakpoint": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "x",
                            "prompt_cache_breakpoint": {"mode": "future"},
                        }
                    ],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "output text has unknown metadata": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "x", "unknown": True}],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "output text annotations are not a list": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "x", "annotations": {}}],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "output text logprobs are not a list": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "x", "logprobs": {}}],
                }
            ]
        },
        "rejected invalid_text_block",
    ),
    "invalid refusal block": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": 1}],
                }
            ]
        },
        "rejected invalid_refusal_block",
    ),
    "refusal on input role": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "refusal", "refusal": "no"}],
                }
            ]
        },
        "rejected invalid_refusal_role",
    ),
    "malformed refusal on input role": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "refusal", "refusal": 1}],
                }
            ]
        },
        "rejected invalid_refusal_block",
    ),
    "image on assistant role": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.test/image.png",
                        }
                    ],
                }
            ]
        },
        "rejected unsupported_assistant_image",
    ),
    "non-string agent root ciphertext": (
        {
            "input": [
                {
                    "type": "agent_message",
                    "author": "planner",
                    "recipient": "user",
                    "content": [],
                    "encrypted_content": 1,
                }
            ]
        },
        "rejected invalid_encrypted_content",
    ),
    "assistant scalar content": (
        {"input": [{"type": "message", "role": "assistant", "content": 1}]},
        "rejected invalid_content",
    ),
    "assistant non-object block": (
        {"input": [{"type": "message", "role": "assistant", "content": [1]}]},
        "rejected invalid_content_block",
    ),
    "assistant unknown block": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "future_content"}],
                }
            ]
        },
        "rejected unknown_content_type",
    ),
    "assistant empty typed text": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            ]
        },
        "rejected empty_portable_input",
    ),
    "assistant empty content list": (
        {"input": [{"type": "message", "role": "assistant", "content": []}]},
        "rejected empty_portable_content",
    ),
    "assistant empty scalar text": (
        {"input": [{"type": "message", "role": "assistant", "content": ""}]},
        "rejected empty_text_content",
    ),
    "non-string tool root ciphertext": (
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "c",
                    "name": "f",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "c",
                    "output": [],
                    "encrypted_content": 1,
                },
            ]
        },
        "rejected invalid_encrypted_content",
    ),
    "invalid image field": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.test/image.png",
                            "unexpected": True,
                        }
                    ],
                }
            ]
        },
        "rejected invalid_image_block",
    ),
    "invalid image detail": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.test/image.png",
                            "detail": "future",
                        }
                    ],
                }
            ]
        },
        "rejected invalid_image_detail",
    ),
    "invalid message role": (
        {"input": [{"type": "message", "role": "tool", "content": "x"}]},
        "rejected invalid_message_role",
    ),
    "invalid message phase": (
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "phase": "commentary",
                    "content": "x",
                }
            ]
        },
        "rejected invalid_message_phase",
    ),
    "invalid agent identity": (
        {"input": [{"type": "agent_message", "author": "planner", "content": "x"}]},
        "rejected invalid_agent_message",
    ),
    "invalid agent phase": (
        {
            "input": [
                {
                    "type": "agent_message",
                    "author": "planner",
                    "recipient": "user",
                    "phase": "future",
                    "content": "x",
                }
            ]
        },
        "rejected invalid_agent_phase",
    ),
    "invalid call id": (
        {"input": [{"type": "function_call", "call_id": "", "name": "f", "arguments": "{}"}]},
        "rejected invalid_call_id",
    ),
    "invalid call": (
        {"input": [{"type": "function_call", "call_id": "c", "name": "", "arguments": "{}"}]},
        "rejected invalid_call",
    ),
    "invalid namespace": (
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "c",
                    "name": "f",
                    "arguments": "{}",
                    "namespace": "",
                }
            ]
        },
        "rejected invalid_namespace",
    ),
    "invalid call caller": (
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "c",
                    "name": "f",
                    "arguments": "{}",
                    "caller": "not-an-object",
                }
            ]
        },
        "rejected invalid_caller",
    ),
    "duplicate output": (
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "c",
                    "name": "f",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "c", "output": "first"},
                {"type": "function_call_output", "call_id": "c", "output": "second"},
            ]
        },
        "rejected duplicate_output",
    ),
    "invalid output caller": (
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "c",
                    "name": "f",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "c",
                    "output": "x",
                    "caller": "not-an-object",
                },
            ]
        },
        "rejected invalid_caller",
    ),
}


class ProviderPortableRejectionTests:
    """Provider-portable grammar rejection contracts."""

    def test_fails_closed_at_each_portable_grammar_boundary(
        self, subtests: pytest.Subtests
    ) -> None:
        for name, (payload, expected_note) in REJECTION_CASES.items():
            with subtests.test(name=name):
                _projection = rewrite.sanitize_responses_body(_body(payload))
                projected = _projection.body
                note = _projection.diagnostic()
                assert projected is None
                assert note == expected_note
