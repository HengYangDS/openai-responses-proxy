"""Provider-portable request projection contracts."""

from __future__ import annotations

import json

from openai_responses_proxy.protocol.replay import input as replay_input
from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body


class TestPortableToolProjection:
    def test_drops_current_codex_local_shell_history_as_one_pair(self) -> None:
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [
                        {
                            "type": "local_shell_call",
                            "id": "lsh_1",
                            "call_id": "call_1",
                            "status": "completed",
                            "action": {
                                "type": "exec",
                                "command": ["printf", "ok"],
                                "timeout_ms": 0,
                                "working_directory": "workspace",
                                "env": {"MODE": "test"},
                                "user": "runner",
                            },
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "ok",
                        },
                        {"type": "message", "role": "user", "content": "continue"},
                    ]
                }
            )
        )

        assert projection.body is not None, projection.diagnostic()
        assert json.loads(projection.body)["input"] == [
            {"type": "message", "role": "user", "content": "continue"}
        ]
        assert projection.metrics.changed_items == 2

    def test_rejects_unpaired_local_shell_call(self) -> None:
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [
                        {
                            "type": "local_shell_call",
                            "call_id": "call_1",
                            "status": "completed",
                            "action": {"type": "exec", "command": ["printf", "ok"]},
                        },
                        {"type": "message", "role": "user", "content": "continue"},
                    ]
                }
            )
        )

        assert projection.body is None
        assert projection.diagnostic() == "rejected incomplete_local_shell_pair"

    def test_rejects_invalid_local_shell_call_fields(self, subtests) -> None:
        cases = {
            "missing status": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "action": {"type": "exec", "command": ["printf", "ok"]},
            },
            "invalid status": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "done",
                "action": {"type": "exec", "command": ["printf", "ok"]},
            },
            "unknown action field": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": ["printf", "ok"],
                    "future": True,
                },
            },
            "boolean timeout": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": ["printf", "ok"],
                    "timeout_ms": True,
                },
            },
            "negative timeout": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": ["printf", "ok"],
                    "timeout_ms": -1,
                },
            },
            "overflow timeout": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": ["printf", "ok"],
                    "timeout_ms": 2**64,
                },
            },
            "invalid environment": {
                "type": "local_shell_call",
                "call_id": "call_1",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": ["printf", "ok"],
                    "env": {"COUNT": 1},
                },
            },
        }
        output = {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "ok",
        }

        for name, call in cases.items():
            with subtests.test(name=name):
                projection = rewrite.sanitize_responses_body(_body({"input": [call, output]}))

                assert projection.body is None
                assert projection.diagnostic() == "rejected invalid_local_shell_call"

    def test_marks_paired_empty_tool_outputs_without_admitting_empty_dialogue(
        self, subtests
    ) -> None:
        cases = (
            ("function_call", "arguments", "function_call_output"),
            ("custom_tool_call", "input", "custom_tool_call_output"),
        )
        for call_type, argument_field, output_type in cases:
            with subtests.test(call_type=call_type):
                _projection = rewrite.sanitize_responses_body(
                    _body(
                        {
                            "input": [
                                {
                                    "type": call_type,
                                    "call_id": "empty-result",
                                    "name": "bounded-test-tool",
                                    argument_field: "{}",
                                },
                                {
                                    "type": output_type,
                                    "call_id": "empty-result",
                                    "output": "",
                                },
                            ]
                        }
                    )
                )
                projected_raw = _projection.body
                note = _projection.diagnostic()

                assert projected_raw is not None, note
                projected = json.loads(projected_raw)["input"]
                assert projected[0]["call_id"] == projected[1]["call_id"]
                assert projected[1]["output"] == replay_input.EMPTY_TOOL_OUTPUT_MARKER
                assert "empty_tool_outputs=1" in note

        _projection = rewrite.sanitize_responses_body(
            _body({"input": [{"type": "message", "role": "assistant", "content": ""}]})
        )
        projected = _projection.body
        note = _projection.diagnostic()
        assert projected is None
        assert note == "rejected empty_text_content"

        for label, output in (("missing", None), ("null", None)):
            item = {"type": "function_call_output", "call_id": "empty-result"}
            if label == "null":
                item["output"] = output
            _projection = rewrite.sanitize_responses_body(
                _body(
                    {
                        "input": [
                            {
                                "type": "function_call",
                                "call_id": "empty-result",
                                "name": "bounded-test-tool",
                                "arguments": "{}",
                            },
                            item,
                        ]
                    }
                )
            )
            projected = _projection.body
            note = _projection.diagnostic()
            with subtests.test(output=label):
                assert projected is None
                assert note == "rejected invalid_content"

    def test_projects_namespaced_function_output_without_forwarding_namespace(
        self,
    ) -> None:
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [
                        {
                            "type": "function_call",
                            "call_id": "namespaced-call",
                            "name": "read_resource",
                            "namespace": "workspace",
                            "arguments": "{}",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "namespaced-call",
                            "name": "read_resource",
                            "namespace": "workspace",
                            "output": "available",
                        },
                    ]
                }
            )
        )

        assert projection.body is not None, projection.diagnostic()
        assert json.loads(projection.body)["input"] == [
            {
                "type": "function_call",
                "call_id": "namespaced-call",
                "name": "read_resource",
                "namespace": "workspace",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "namespaced-call",
                "output": "available",
            },
        ]

    def test_rejects_namespace_on_custom_tool_output(self) -> None:
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [
                        {
                            "type": "custom_tool_call",
                            "call_id": "custom-call",
                            "name": "apply_patch",
                            "input": "patch",
                        },
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "custom-call",
                            "namespace": "workspace",
                            "output": "done",
                        },
                    ]
                }
            )
        )

        assert projection.body is None
        assert projection.diagnostic() == "rejected unknown_output_field"
