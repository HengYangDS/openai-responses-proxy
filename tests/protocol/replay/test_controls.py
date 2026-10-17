"""Provider-portable request projection contracts."""

from __future__ import annotations

import json

from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body


class TestPortableRequestControls:
    def test_preserves_remote_compaction_trigger_as_a_request_control(self) -> None:
        raw = _body(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "compact this"},
                    {"type": "compaction_trigger"},
                ]
            }
        )

        projection = rewrite.sanitize_responses_body(raw)

        assert projection.body is not None, projection.diagnostic()
        assert json.loads(projection.body)["input"] == [
            {"type": "message", "role": "user", "content": "compact this"},
            {"type": "compaction_trigger"},
        ]
        assert projection.status == "projected"

    def test_preserves_current_turn_tool_catalog_instead_of_silently_removing_tools(self) -> None:
        catalog = {
            "type": "additional_tools",
            "id": "catalog-current-turn",
            "role": "developer",
            "tools": [
                {
                    "type": "namespace",
                    "name": "functions",
                    "description": "Available commands",
                    "tools": [{"type": "function", "name": "run", "parameters": {}}],
                }
            ],
        }
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [catalog, {"type": "message", "role": "user", "content": "run it"}],
                    "prompt_cache_key": "provider-bound",
                }
            )
        )

        assert projection.body is not None, projection.diagnostic()
        assert json.loads(projection.body)["input"] == [
            catalog,
            {"type": "message", "role": "user", "content": "run it"},
        ]

    def test_rejects_malformed_current_turn_tool_catalog(self, subtests) -> None:
        valid = {"type": "additional_tools", "role": "developer", "tools": [{"type": "namespace"}]}
        invalid = (
            ("non-list catalog", {**valid, "tools": "not a list"}, "invalid_additional_tools"),
            ("non-object tool", {**valid, "tools": ["not an object"]}, "invalid_additional_tools"),
            ("invalid role", {**valid, "role": 1}, "invalid_additional_tools"),
            ("invalid item identity", {**valid, "id": 1}, "invalid_additional_tools"),
            ("unknown field", {**valid, "unproved": True}, "unknown_additional_tools_field"),
        )
        for name, catalog, reason in invalid:
            with subtests.test(case=name):
                projection = rewrite.sanitize_responses_body(
                    _body(
                        {"input": [catalog, {"type": "message", "role": "user", "content": "run"}]}
                    )
                )
                assert projection.body is None
                assert projection.reason == reason

    def test_empty_current_tool_catalog_preserves_native_compaction_intent(self) -> None:
        catalog = {"type": "additional_tools", "role": "developer", "tools": []}
        raw = _body({"input": [catalog, {"type": "compaction_trigger"}]})
        projected = rewrite.sanitize_responses_body(raw)
        assert projected.body is not None, projected.diagnostic()
        assert json.loads(projected.body)["input"] == [catalog, {"type": "compaction_trigger"}]

    def test_rejects_compaction_trigger_with_unknown_fields(self) -> None:
        projection = rewrite.sanitize_responses_body(
            _body({"input": [{"type": "compaction_trigger", "unexpected": True}]})
        )

        assert projection.body is None
        assert projection.diagnostic() == "rejected unknown_compaction_trigger_field"

    def test_preserves_clean_string_input_while_removing_top_level_bindings(
        self,
    ) -> None:
        raw = _body(
            {
                "input": "hello",
                "previous_response_id": "provider-response",
                "conversation": "provider-conversation",
            }
        )

        _projection = rewrite.sanitize_responses_body(raw)
        projected_raw = _projection.body
        _note = _projection.diagnostic()

        assert projected_raw is not None, _note
        assert json.loads(projected_raw) == {"input": "hello", "store": False}

    def test_preserves_bare_compaction_trigger_as_portable_control_input(self) -> None:
        projection = rewrite.sanitize_responses_body(
            _body(
                {
                    "input": [
                        {"type": "compaction_trigger"},
                        {"type": "message", "role": "user", "content": "continue"},
                    ]
                }
            )
        )

        assert projection.body is not None, projection.diagnostic()
        projected = json.loads(projection.body)
        assert projected["input"] == [
            {"type": "compaction_trigger"},
            {"type": "message", "role": "user", "content": "continue"},
        ]

    def test_forces_stateless_responses_even_when_store_is_absent_or_true(self, subtests) -> None:
        for supplied in (False, True, None):
            with subtests.test(store=supplied):
                payload = {"input": "continue"}
                if supplied is not None:
                    payload["store"] = supplied

                _projection = rewrite.sanitize_responses_body(_body(payload))
                projected_raw = _projection.body
                note = _projection.diagnostic()

                assert projected_raw is not None, note
                assert json.loads(projected_raw) == {
                    "input": "continue",
                    "store": False,
                }
                if supplied is True:
                    assert "store_normalized=True" in note
