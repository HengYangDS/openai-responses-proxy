"""Contracts for the pure Responses input-compatibility policy."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import cast

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


def _recover(payload: Mapping[str, object], budget: int):
    raw = _json(payload)
    recovery, metrics = input_recovery.build_recovery(raw, budget if budget > 0 else len(raw))
    return raw, recovery, metrics


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
        projected = input_recovery.diagnostic_dict(diagnostic)
        rendered = input_recovery.format_diagnostic(projected)
        disclosure = rendered + json.dumps(projected, sort_keys=True)
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

        projected = [
            input_recovery.diagnostic_dict(diagnostic_for(repetitions)) for repetitions in (2, 4)
        ]
        assert [
            cast("dict[str, str]", item["item_types"])["custom_tool_call"] for item in projected
        ] == ["2-4", "5-16"]
        for item in projected:
            assert item["input_items_bucket"] == "5-16"
            assert item["matched_pairs"]
            assert item["unmatched_calls"]
            assert item["missing_call_ids"]
            assert "first_incompatible_index" not in item
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

    def test_mapping_projection_rejects_untrusted_buckets_and_flags(self) -> None:
        rendered = input_recovery.format_diagnostic(
            {
                "input_items_bucket": "private",
                "item_types": {"message": "1", "secret": "private", 1: "1"},
                "content_types": "not-a-mapping",
                "matched_pairs": True,
                "unmatched_calls": "true",
                "first_incompatible_reason": "invalid",
            }
        )
        assert "input_items_bucket=0" in rendered
        assert "item_types=message:1" in rendered
        assert "content_types=-" in rendered
        assert "matched_pairs=present" in rendered
        assert "unmatched_calls=absent" in rendered
        assert "secret" not in rendered


class TestDialogueRecovery:
    """Build one smaller current-dialogue request without losing turn controls."""

    def test_recovery_retains_current_turn_controls_with_latest_dialogue(self) -> None:
        catalog = {
            "type": "additional_tools",
            "role": "developer",
            "tools": [{"type": "namespace", "name": "functions", "tools": []}],
        }
        trigger = {"type": "compaction_trigger"}
        current = {"type": "message", "role": "user", "content": "current request"}
        old = {"type": "message", "role": "user", "content": "old request"}

        _, recovery, metrics = _recover({"input": [catalog, old, current, trigger]}, 0)

        assert recovery is not None
        assert json.loads(recovery)["input"] == [catalog, current, trigger]
        assert metrics is not None
        assert metrics.dropped_input_items == 1
        assert metrics.retained_messages == 1

    def test_preserves_envelope_and_reports_exact_reduction(self) -> None:
        payload: dict[str, object] = {
            "instructions": "top-level policy",
            "previous_response_id": "stale",
            "conversation": {"id": "stale"},
            "prompt_cache_key": "stale",
            "include": ["reasoning.encrypted_content", "other"],
            "input": [
                {"type": "message", "role": "user", "content": "current"},
                {"type": "message", "role": "developer", "content": "late policy"},
                {
                    "type": "custom_tool_call",
                    "call_id": "call",
                    "name": "exec",
                    "input": "{}",
                },
            ],
        }
        raw, recovery, metrics = _recover(payload, 512 * 1024)
        assert recovery is not None
        assert metrics is not None
        assert recovery is not None
        assert metrics is not None
        recovered = json.loads(recovery)
        assert recovered["instructions"] == "top-level policy"
        assert recovered["store"] is False
        assert recovered["input"] == [payload["input"][0], payload["input"][1]]
        assert recovered["include"] == ["other"]
        assert not {"previous_response_id", "conversation", "prompt_cache_key"} & recovered.keys()
        assert metrics == input_recovery.RecoveryMetrics(len(raw), len(recovery), 2, 1, True)

    def test_latest_role_messages_keep_original_relative_order(self) -> None:
        cases = (
            (
                [
                    {"type": "message", "role": "user", "content": "old user"},
                    {"type": "message", "role": "system", "content": "current system"},
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "old developer",
                    },
                    {"type": "message", "role": "user", "content": "current user"},
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current developer",
                    },
                    {"type": "reasoning", "summary": []},
                ],
                (1, 3, 4),
            ),
            (
                [
                    {"type": "message", "role": "system", "content": "old system"},
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "old developer",
                    },
                    {"type": "message", "role": "user", "content": "old user"},
                    {"type": "message", "role": "user", "content": "current user"},
                    {"type": "message", "role": "system", "content": "current system"},
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current developer",
                    },
                    {"type": "reasoning", "summary": []},
                ],
                (3, 4, 5),
            ),
        )
        recovered: list[tuple[object, object]] = []
        for items, retained in cases:
            _, recovery, _ = _recover({"input": items}, 0)
            assert recovery is not None
            recovered.append((json.loads(recovery)["input"], [items[index] for index in retained]))
        assert [actual for actual, _ in recovered] == [expected for _, expected in recovered]

    def test_projects_only_nonempty_text_content(self) -> None:
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": "https://x",
                        },
                        {"type": "output_text", "text": "current"},
                        {"type": "input_text", "text": ""},
                        1,
                    ],
                },
                {"type": "reasoning", "summary": []},
            ]
        }
        _, recovery, _ = _recover(payload, 0)
        assert recovery is not None
        assert json.loads(recovery)["input"] == [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "current"}],
            }
        ]

    def test_rejects_unrepresentable_or_non_reducing_requests(self) -> None:
        valid = _json(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "current"},
                    {"type": "reasoning", "summary": []},
                ]
            }
        )
        cases = (
            (b"not-json", 100),
            (b"[]", 100),
            (b'{"input":[]}', 100),
            (b'{"input":[{"type":"message","role":"developer","content":"x"}]}', 100),
            (
                _json({"input": [{"type": "message", "role": "user", "content": "current"}]}),
                100,
            ),
            (
                _json(
                    {
                        "input": [
                            {"type": "message", "role": "user", "content": None},
                            {"type": "reasoning"},
                        ]
                    }
                ),
                100,
            ),
            (
                _json(
                    {
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [1, {"type": "input_text", "text": ""}],
                            },
                            {"type": "reasoning"},
                        ]
                    }
                ),
                100,
            ),
            (valid, 0),
            (valid, True),
            (valid, 10),
        )
        assert [input_recovery.build_recovery(raw, budget) for raw, budget in cases] == [
            (None, None)
        ] * len(cases)

    def test_recovery_rejects_unencodable_envelope_values(self) -> None:
        raw = json.dumps(
            {
                "input": [{"type": "message", "role": "user", "content": "current"}],
                "previous_response_id": "stale",
                "private": "\ud800",
            },
            separators=(",", ":"),
        ).encode()
        assert input_recovery.build_recovery(raw, len(raw)) == (None, None)
