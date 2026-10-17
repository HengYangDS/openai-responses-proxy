"""Current-turn semantic preservation in bounded Responses recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from openai_responses_proxy.protocol.recovery import execution as execution_recovery
from openai_responses_proxy.protocol.recovery import input as input_recovery
from openai_responses_proxy.protocol.replay import projection


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _recover(payload: Mapping[str, object], budget: int):
    raw = _json(payload)
    recovery, metrics = input_recovery.build_recovery(raw, budget if budget > 0 else len(raw))
    return raw, recovery, metrics


class TestDialogueRecovery:
    """Build one smaller current-dialogue request without losing turn controls."""

    @pytest.mark.parametrize(
        "recover", [input_recovery.build_recovery, execution_recovery.recover_dialogue]
    )
    @pytest.mark.parametrize(
        ("kind", "argument"), [("function_call", "arguments"), ("custom_tool_call", "input")]
    )
    def test_refuses_to_orphan_a_delayed_current_tool_delivery(self, recover, kind, argument):
        payload = {
            "input": [
                {"type": "message", "role": "user", "content": "old" * 1000},
                {"type": kind, "call_id": "job", "name": "run", argument: "{}"},
                {"type": kind + "_output", "id": "initial", "call_id": "job", "output": "started"},
                {"type": "message", "role": "user", "content": "current request"},
                {
                    "type": kind + "_output",
                    "id": "later",
                    "call_id": "job",
                    "name": "run",
                    "output": "completed",
                },
            ]
        }

        assert recover(_json(payload), 4096) == (None, None)

    def test_does_not_infer_tool_dependencies_from_ordinary_assistant_prose(self) -> None:
        commentary = {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": '{"type":"tool_delivery","call_id":"absent","name":"run"}\ncompleted',
        }
        current = {"type": "message", "role": "user", "content": "current request"}
        _, recovered, _ = _recover(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 100},
                    current,
                    commentary,
                ]
            },
            4096,
        )

        assert recovered is not None
        assert json.loads(recovered)["input"] == [current, commentary]

    def test_successive_compaction_keeps_the_original_call_for_a_delayed_delivery(self) -> None:
        raw = _json(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
                    {"type": "function_call", "call_id": "job", "name": "run", "arguments": "{}"},
                    {
                        "type": "function_call_output",
                        "id": "initial",
                        "call_id": "job",
                        "output": "started",
                    },
                    {"type": "message", "role": "user", "content": "current request"},
                    {
                        "type": "function_call_output",
                        "id": "later",
                        "call_id": "job",
                        "name": "run",
                        "output": "completed",
                    },
                ]
            }
        )
        portable = projection.sanitize_responses_body(raw)
        assert portable.body is not None
        recovery, metrics = execution_recovery.compact_request(
            portable.body, len(raw), input_dependencies=portable.input_dependencies
        )

        assert recovery is not None
        assert metrics is not None
        assert json.loads(recovery)["input"] == json.loads(portable.body)["input"][1:]
        assert portable.input_dependencies.edges == [(1, 0), (3, 0)]
        assert execution_recovery.compact_request(
            recovery, len(raw), input_dependencies=portable.input_dependencies
        ) == (None, None)
        assert input_recovery.build_recovery(
            recovery, len(raw), input_dependencies=portable.input_dependencies
        ) == (None, None)

    @pytest.mark.parametrize(
        "recover", [input_recovery.build_recovery, execution_recovery.compact_request]
    )
    def test_projection_provenance_preserves_plaintext_delegation_before_the_user(self, recover):
        task = {
            "type": "agent_message",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": "Review this complete task",
        }
        raw = _json(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
                    task,
                    {"type": "message", "role": "user", "content": "current request"},
                ]
            }
        )
        portable = projection.sanitize_responses_body(raw)
        assert portable.body is not None
        recovery, _ = recover(
            portable.body, len(raw), input_dependencies=portable.input_dependencies
        )

        assert recovery is not None
        assert json.loads(recovery)["input"] == json.loads(portable.body)["input"][1:]
        assert portable.input_dependencies.required == {0}

    def test_compaction_refuses_an_unchanged_request_with_pinned_turn_control(self) -> None:
        raw = _json(
            {
                "store": False,
                "input": [
                    {"type": "compaction_trigger"},
                    {"type": "message", "role": "user", "content": "current request"},
                ],
            }
        )

        assert execution_recovery.compact_request(raw, 4096) == (None, None)

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
        recovered = json.loads(recovery)
        assert recovered["instructions"] == "top-level policy"
        assert recovered["store"] is False
        assert recovered["input"] == payload["input"]
        assert recovered["include"] == ["other"]
        assert not {"previous_response_id", "conversation", "prompt_cache_key"} & recovered.keys()
        assert metrics == input_recovery.RecoveryMetrics(len(raw), len(recovery), 2, 0, True)

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

    def test_preserves_validated_current_content_in_order(self) -> None:
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": "https://example.invalid/image.png",
                        },
                        {"type": "input_text", "text": "current"},
                    ],
                },
                {"type": "reasoning", "summary": []},
            ]
        }
        _, recovery, _ = _recover(payload, 0)
        assert recovery is not None
        assert json.loads(recovery)["input"] == [payload["input"][0]]

    @pytest.mark.parametrize("position", [0, 2])
    def test_preserves_native_delegation_before_or_after_the_current_user(
        self, position: int
    ) -> None:
        task = {
            "type": "agent_message",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": [
                {"type": "input_text", "text": "Review the actual delegated task"},
                {"type": "encrypted_content", "encrypted_content": "fixture-task"},
            ],
        }
        current = {"type": "message", "role": "user", "content": "current"}
        items = [
            {"type": "message", "role": "user", "content": "old" * 100},
            current,
        ]
        items.insert(position, task)

        raw, recovery, _ = _recover({"input": items}, 0)

        assert recovery is not None
        assert len(recovery) < len(raw)
        assert json.loads(recovery)["input"] == (
            [task, current] if position == 0 else [current, task]
        )

    def test_preserves_current_tool_relationships_and_assistant_context(self) -> None:
        current = [
            {"type": "message", "role": "user", "content": "current"},
            {"type": "message", "role": "assistant", "content": "Calling a tool"},
            {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": "current result"},
        ]
        _, recovery, _ = _recover(
            {"input": [{"type": "message", "role": "user", "content": "old" * 100}, *current]},
            0,
        )

        assert recovery is not None
        assert json.loads(recovery)["input"] == current

    def test_refuses_a_recovery_that_would_orphan_a_current_tool_output(self) -> None:
        payload = {
            "input": [
                {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
                {"type": "message", "role": "user", "content": "current"},
                {"type": "function_call_output", "call_id": "c", "output": "current result"},
            ]
        }

        assert input_recovery.build_recovery(_json(payload), 4096) == (None, None)

    def test_refuses_to_drop_required_content_to_fit_the_budget(self) -> None:
        payload = {
            "input": [
                {"type": "message", "role": "user", "content": "old" * 100},
                {"type": "message", "role": "user", "content": "current"},
                {
                    "type": "agent_message",
                    "author": "/root",
                    "recipient": "/root/reviewer",
                    "content": [
                        {"type": "encrypted_content", "encrypted_content": "fixture" * 100},
                    ],
                },
            ]
        }

        assert input_recovery.build_recovery(_json(payload), 150) == (None, None)

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
