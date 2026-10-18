"""Contracts for the pure Responses input-compatibility policy."""

from __future__ import annotations

import json
import re

import pytest

from openai_responses_proxy.protocol.recovery import input as input_recovery
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body

EXACT_ERROR = {
    "error": {
        "message": "invalid request body: Invalid 'input': value did not match any expected variant",
        "type": "invalid_request_error",
        "param": "",
        "code": "validation_error",
    }
}


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _diagnose(item: object) -> input_recovery.InputDiagnostic:
    return input_recovery.diagnose(_json({"input": [item]}))


def _shape_hash(payload: object) -> str:
    return input_recovery.diagnose(_json(payload)).shape_sha256


class TestExactErrorContract:
    """Admit only the complete observed upstream error."""

    def test_exact_contract_boundary(self) -> None:
        exact = _json(EXACT_ERROR)
        with_metadata = {**EXACT_ERROR, "request_id": "opaque"}
        changed = json.loads(exact)
        changed["error"]["request_id"] = "unstable"
        cases = (
            (400, exact, True),
            (400, _json(with_metadata), True),
            (422, exact, False),
            (400, _json(changed), False),
            (400, b"not-json", False),
            (400, b"[]", False),
            (400, b'{"error":null}', False),
        )
        assert [
            input_recovery.is_exact_validation_error(status, body) for status, body, _ in cases
        ] == [expected for _, _, expected in cases]


class TestInputDiagnostic:
    """Keep structural diagnostics bounded, categorical, and content-free."""

    @pytest.mark.parametrize(
        ("payload", "portable"),
        [
            pytest.param({"input": "current"}, True, id="string-input"),
            pytest.param(
                {"input": [{"role": "user", "content": "current"}]},
                True,
                id="optional-message-type",
            ),
            pytest.param(
                {
                    "input": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": ""},
                            ],
                        },
                        {"type": "message", "role": "user", "content": "current"},
                    ]
                },
                True,
                id="empty-assistant-placeholder",
            ),
            pytest.param(
                {
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "https://example.invalid/image.png",
                                },
                            ],
                        }
                    ]
                },
                True,
                id="image-default-detail",
            ),
            pytest.param(
                {
                    "input": [
                        {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
                        {
                            "type": "function_call_output",
                            "call_id": "c",
                            "output": None,
                            "encrypted_content": "private-ciphertext",
                        },
                    ]
                },
                True,
                id="ciphertext-only-output",
            ),
            pytest.param(
                {
                    "input": [
                        {
                            "type": "function_call_output",
                            "id": "delivery",
                            "name": "notify",
                            "namespace": "tools",
                            "output": "delivered",
                        }
                    ]
                },
                True,
                id="detached-delivery",
            ),
            pytest.param({"input": None}, False, id="invalid-input"),
            pytest.param({"input": "current", "include": [1]}, False, id="invalid-include"),
            pytest.param(
                {
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": "current",
                            "private-field": "private-value",
                        }
                    ]
                },
                False,
                id="unknown-message-field",
            ),
        ],
    )
    def test_diagnosis_consumes_request_projection(self, payload: object, portable: bool) -> None:
        raw = _json(payload)
        projection = sanitize_responses_body(raw)
        assert (projection.body is not None) is portable, projection.reason
        diagnostic = input_recovery.diagnose(raw)
        assert diagnostic.first_incompatible_reason == (projection.reason or "")

    def test_private_names_values_and_cardinality_are_erased(self) -> None:
        secret = "private-value-never-log"
        diagnostic = _diagnose(
            {
                "type": "private-extension-name",
                "role": "private-role",
                "private-field": secret,
            }
        )
        rendered = input_recovery.format_diagnostic(diagnostic)
        disclosure = rendered + repr(diagnostic)
        assert not {secret, "private-extension-name", "private-field"} & set(disclosure.split())

        def diagnostic_for(repetitions: int) -> input_recovery.InputDiagnostic:
            items: list[dict[str, object]] = [
                {"type": "message", "role": "user", "content": "current"},
                {
                    "type": "function_call",
                    "call_id": "paired",
                    "name": "f",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "paired", "output": "ok"},
            ]
            for index in range(repetitions):
                items += [
                    {
                        "type": "custom_tool_call",
                        "call_id": f"unmatched-{index}",
                        "name": "f",
                        "input": "{}",
                    },
                    {"type": "custom_tool_call", "name": "f", "input": "{}"},
                ]
            return input_recovery.diagnose(_json({"input": items}))

        diagnostics = [diagnostic_for(repetitions) for repetitions in (2, 4)]
        assert [item.item_types["custom_tool_call"] for item in diagnostics] == ["2-4", "5-16"]
        for item in diagnostics:
            assert item.input_items_bucket == "5-16"
            assert item.matched_pairs
            assert item.unmatched_calls
            assert item.missing_call_ids
            assert "first_incompatible_index" not in input_recovery.format_diagnostic(item)
        rendered = input_recovery.format_diagnostic(diagnostic_for(2))
        assert "input_items=" not in rendered
        assert not re.search(r"(?:matched_pairs|unmatched_calls|missing_call_ids)=\d", rendered)

    def test_shape_hash_erases_order_repetition_and_exact_sizes(self) -> None:
        user = {"type": "message", "role": "user", "content": "current"}
        reasoning = {"type": "reasoning", "summary": []}
        pairs = (
            (
                {"input": [user, reasoning]},
                {"input": [reasoning, user, user, reasoning]},
            ),
            (
                {"input": [], "private": ["value"] * 17},
                {"input": [], "private": ["value"] * 33},
            ),
            (
                {"input": [], "private": {"one": 1}},
                {"input": [], "private": {"one": 1, "two": 2, "three": 3}},
            ),
            (
                {
                    "input": [],
                    "private": [
                        {"type": f"private-{index}", "field": [None] * index} for index in range(33)
                    ],
                },
                {
                    "input": [],
                    "private": [
                        {"type": f"private-{index}", "field": [None] * index} for index in range(34)
                    ],
                },
            ),
            (
                {"input": [], "private": [[[[[[[True]]]]]]]},
                {"input": [], "private": [[[[[[[False, None]]]]]]]},
            ),
        )
        assert [_shape_hash(first) for first, _ in pairs] == [
            _shape_hash(second) for _, second in pairs
        ]

    def test_pair_state_and_tool_search_contract(self) -> None:
        valid_search = [
            {"type": "tool_search_call", "call_id": "search", "arguments": {}},
            {"type": "tool_search_output", "call_id": "search", "tools": []},
            {"type": "message", "role": "user", "content": "current"},
        ]
        assert (
            input_recovery.diagnose(_json({"input": valid_search})).first_incompatible_reason == ""
        )
        assert (
            _diagnose(
                {"type": "tool_search_output", "call_id": "search", "output": []}
            ).first_incompatible_reason
            == "empty_portable_input"
        )

        diagnostic = input_recovery.diagnose(
            _json(
                {
                    "input": [
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "early",
                            "output": "x",
                        },
                        {
                            "type": "custom_tool_call",
                            "call_id": "dup",
                            "name": "f",
                            "input": "{}",
                        },
                        {
                            "type": "custom_tool_call",
                            "call_id": "dup",
                            "name": "f",
                            "input": "{}",
                        },
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "dup",
                            "output": "x",
                        },
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "dup",
                            "output": "x",
                        },
                        {"type": "custom_tool_call", "name": "f", "input": "{}"},
                    ]
                }
            )
        )
        assert (
            diagnostic.outputs_before_calls,
            diagnostic.duplicate_calls,
            diagnostic.duplicate_outputs,
            diagnostic.missing_call_ids,
            diagnostic.unmatched_outputs,
        ) == (True,) * 5

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            (b"not-json", "invalid_json"),
            (b"\xff", "invalid_json"),
            (b"[]", "request_not_object"),
            (b'{"input":null}', "invalid_input"),
            (b'{"input":[1]}', "invalid_item"),
            (b'{"input":[{"type":"future"}]}', "unknown_item_type"),
        ],
    )
    def test_failure_reason_is_the_projection_verdict(self, raw: bytes, reason: str) -> None:
        assert sanitize_responses_body(raw).reason == reason
        assert input_recovery.diagnose(raw).first_incompatible_reason == reason

    @pytest.mark.parametrize(
        ("item_type", "label"),
        [({"private": "value"}, "dict"), (["private"], "list"), ("private-type", "unknown")],
    )
    def test_raw_item_type_is_categorical(self, item_type: object, label: str) -> None:
        assert _diagnose({"type": item_type}).item_types == {label: "1"}

    def test_typed_diagnostic_preserves_the_categorical_log_contract(self) -> None:
        diagnostic = input_recovery.diagnose(
            _json(
                {
                    "input": [
                        {"type": "message", "role": "user", "content": "private"},
                        {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
                        {"type": "function_call_output", "call_id": "c", "output": "private"},
                    ]
                }
            )
        )
        rendered = input_recovery.format_diagnostic(diagnostic)
        assert rendered == (
            "input_items_bucket=2-4 "
            "item_types=function_call:1,function_call_output:1,message:1 "
            "content_types=string:1 matched_pairs=present unmatched_calls=absent "
            "unmatched_outputs=absent outputs_before_calls=absent duplicate_calls=absent "
            "duplicate_outputs=absent missing_call_ids=absent mismatched_output_types=absent "
            "first_incompatible_reason=- "
            "shape_sha256=ffde30a4dd14faa7e6d2e2179624efe22037c708abfba2cc12e131f0f746e5d2"
        )
