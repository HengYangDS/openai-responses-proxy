"""Response item policy closure contracts."""

from __future__ import annotations

import json

import pytest

from openai_responses_proxy.protocol.recovery.execution import tool_pair_boundary_is_safe
from openai_responses_proxy.protocol.recovery.input import diagnose
from openai_responses_proxy.protocol.replay.items import ITEM_POLICIES
from openai_responses_proxy.protocol.replay.items import ProjectionStrategy
from openai_responses_proxy.protocol.replay.items import ToolRelationships
from openai_responses_proxy.protocol.replay.items import classify_item
from openai_responses_proxy.protocol.replay.items import item_types
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body


@pytest.mark.parametrize("item_type", item_types())
def test_every_policy_item_is_diagnosed_as_recognized(item_type: str) -> None:
    diagnostic = diagnose(f'{{"input":[{{"type":"{item_type}"}}]}}'.encode())

    assert diagnostic.first_incompatible_reason != "unknown_item_type"


@pytest.mark.parametrize("role", ["user", "assistant", "developer", "system"])
@pytest.mark.parametrize("structured", [False, True])
def test_easy_input_message_uses_the_standard_optional_discriminator(
    role: str, structured: bool
) -> None:
    content = (
        [{"type": "output_text" if role == "assistant" else "input_text", "text": "value"}]
        if structured
        else "value"
    )
    message = {"role": role, "content": content}
    raw = json.dumps({"input": [message]}).encode()
    explicit = json.dumps({"input": [{"type": "message", **message}]}).encode()

    projected = sanitize_responses_body(raw)

    assert projected.body is not None
    assert projected.body == sanitize_responses_body(explicit).body
    assert diagnose(raw).first_incompatible_reason == ""
    assert diagnose(raw).item_types == {"message": "1"}


def test_recognized_unimplemented_standard_item_is_schema_drift() -> None:
    policy = classify_item("shell_call")

    assert policy is not None
    assert policy.projection is ProjectionStrategy.SCHEMA_DRIFT
    assert policy.rejection_reason == "schema_drift"


@pytest.mark.parametrize(
    ("item_type", "strategy"),
    [
        ("context_compaction", ProjectionStrategy.DROP_REFERENCE),
        ("image_generation_call", ProjectionStrategy.DROP_AUXILIARY),
        ("local_shell_call", ProjectionStrategy.DROP_LOCAL_TOOL),
    ],
)
def test_current_codex_auxiliary_items_have_portable_dispositions(
    item_type: str,
    strategy: ProjectionStrategy,
) -> None:
    policy = classify_item(item_type)

    assert policy is not None
    assert policy.projection is strategy
    assert policy.rejection_reason == ""


def test_current_turn_tool_catalog_is_not_auxiliary_replay_history() -> None:
    policy = classify_item("additional_tools")

    assert policy is not None
    assert policy.projection is ProjectionStrategy.TOOL_CATALOG


@pytest.mark.parametrize("value", [None, "future_item", 1, {}])
def test_unknown_or_invalid_item_has_no_policy(value: object) -> None:
    assert classify_item(value) is None


@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
@pytest.mark.parametrize(
    ("sequence", "valid"),
    [
        (("call", "output"), True),
        (("call",), True),
        (("output",), False),
        (("call", "call", "output"), False),
        (("call", "output", "output"), False),
        (("call", "wrong-output"), False),
        (("call", "missing-id"), False),
    ],
)
def test_projection_diagnosis_and_recovery_share_tool_relationships(
    call_type: str, sequence: tuple[str, ...], *, valid: bool
) -> None:
    policy = classify_item(call_type)
    assert policy is not None
    assert policy.argument_field is not None
    call = {"type": call_type, "call_id": "tool", "name": "fixture", policy.argument_field: "{}"}
    output = {"type": policy.paired_output, "call_id": "tool", "output": "result"}
    other_type = "custom_tool_call" if call_type == "function_call" else "function_call"
    other = classify_item(other_type)
    assert other is not None
    fixtures = {
        "call": call,
        "output": output,
        "wrong-output": {**output, "type": other.paired_output},
        "missing-id": {**output, "call_id": ""},
    }
    items = [fixtures[name] for name in sequence]
    raw = json.dumps({"input": items}).encode()

    assert (sanitize_responses_body(raw).body is not None) is valid
    assert (diagnose(raw).first_incompatible_reason == "") is valid
    assert tool_pair_boundary_is_safe(items, 0) is valid


@pytest.mark.parametrize(
    ("call_type", "output_type"),
    [
        (kind, policy.paired_output)
        for kind, policy in ITEM_POLICIES.items()
        if policy.paired_output
    ],
)
def test_declared_tool_relationship_owns_identity_and_diagnostics(
    call_type: str, output_type: str
) -> None:
    relationships = ToolRelationships()

    assert relationships.observe_item({"type": call_type, "call_id": "tool"}) == ""
    assert relationships.call_type("tool") == call_type
    assert relationships.has_pending_call(call_type)
    assert relationships.diagnostic_flags == {"unmatched_calls"}
    assert relationships.observe_item({"type": output_type, "call_id": "tool"}) == ""
    assert not relationships.has_pending_call(call_type)
    assert relationships.diagnostic_flags == {"matched_pairs"}


@pytest.mark.parametrize(
    ("sequence", "reason"),
    [
        (("call", "output"), None),
        (("call",), None),
        (("output",), "output_before_call"),
        (("output", "call"), "output_before_call"),
        (("call", "call", "output"), "duplicate_call"),
        (("call", "output", "output"), "duplicate_output"),
        (("missing-id",), "missing_call_id"),
    ],
)
def test_provider_local_search_uses_the_shared_relationship_boundary(
    sequence: tuple[str, ...], reason: str | None
) -> None:
    fixtures = {
        "call": {"type": "tool_search_call", "call_id": "search", "execution": "server"},
        "output": {"type": "tool_search_output", "call_id": "search", "execution": "server"},
        "missing-id": {"type": "tool_search_output", "execution": "server"},
    }
    current = {"type": "message", "role": "user", "content": "current"}
    items = [fixtures[name] for name in sequence] + [current]
    raw = json.dumps({"input": items}).encode()

    projection = sanitize_responses_body(raw)

    assert projection.reason == reason
    assert diagnose(raw).first_incompatible_reason == (reason or "")
    assert tool_pair_boundary_is_safe(items, 0) is (reason is None)
    if reason is None:
        assert projection.body is not None
        assert json.loads(projection.body)["input"] == [current]


def test_first_call_identity_survives_a_conflicting_replay() -> None:
    relationships = ToolRelationships()

    assert relationships.observe_item({"type": "function_call", "call_id": "tool"}) == ""
    assert (
        relationships.observe_item({"type": "custom_tool_call", "call_id": "tool"})
        == "duplicate_call"
    )
    assert relationships.call_type("tool") == "function_call"
    assert relationships.observe_item({"type": "function_call_output", "call_id": "tool"}) == ""
    assert relationships.diagnostic_flags == {"duplicate_calls", "matched_pairs"}


@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
def test_named_distinct_deliveries_share_projection_diagnosis_and_recovery(call_type: str) -> None:
    policy = classify_item(call_type)
    assert policy is not None
    assert policy.argument_field is not None
    items = [
        {"type": call_type, "call_id": "job", "name": "run", policy.argument_field: "{}"},
        {"type": policy.paired_output, "call_id": "job", "id": "first", "output": "Running"},
        {
            "type": policy.paired_output,
            "call_id": "job",
            "id": "next",
            "name": "run",
            "output": "Complete",
        },
    ]
    raw = json.dumps({"input": items}).encode()

    assert sanitize_responses_body(raw).body is not None
    diagnostic = diagnose(raw)
    assert diagnostic.first_incompatible_reason == ""
    assert not diagnostic.duplicate_outputs
    assert tool_pair_boundary_is_safe(items, 0)
    assert not tool_pair_boundary_is_safe(items, 1)


@pytest.mark.parametrize(
    "replacement",
    [{"id": "first"}, {"id": ""}, {"id": None}, {"name": "other"}, {"name": None}],
)
def test_repeated_outputs_need_distinct_identity_and_matching_name(replacement) -> None:
    items = [
        {"type": "custom_tool_call", "call_id": "job", "name": "run", "input": "{}"},
        {"type": "custom_tool_call_output", "call_id": "job", "id": "first", "output": "A"},
        {
            "type": "custom_tool_call_output",
            "call_id": "job",
            "id": "next",
            "name": "run",
            "output": "B",
            **replacement,
        },
    ]

    result = sanitize_responses_body(json.dumps({"input": items}).encode())

    assert result.body is None
    assert result.diagnostic() == "rejected duplicate_output"


def test_delivery_identity_cannot_be_reused_as_another_calls_first_result() -> None:
    items = [
        {"type": "custom_tool_call", "call_id": "a", "name": "run", "input": "{}"},
        {"type": "custom_tool_call_output", "call_id": "a", "output": "Running"},
        {
            "type": "custom_tool_call_output",
            "call_id": "a",
            "id": "delivery",
            "name": "run",
            "output": "Done",
        },
        {"type": "custom_tool_call", "call_id": "b", "name": "run", "input": "{}"},
        {"type": "custom_tool_call_output", "call_id": "b", "id": "delivery", "output": "Other"},
    ]

    result = sanitize_responses_body(json.dumps({"input": items}).encode())

    assert result.body is None
    assert result.diagnostic() == "rejected duplicate_output"
    assert not tool_pair_boundary_is_safe(items, 0)


def test_standalone_delivery_is_not_an_orphaned_tool_pair() -> None:
    delivery = {
        "type": "function_call_output",
        "id": "delivery",
        "name": "wait",
        "namespace": "functions",
        "output": "Task completed",
    }
    items = [delivery, {"type": "message", "role": "user", "content": "continue"}]
    raw = json.dumps({"input": items}).encode()

    projected = sanitize_responses_body(raw)
    diagnostic = diagnose(raw)

    assert projected.body is not None
    assert projected.input_dependencies.edges == []
    assert diagnostic.first_incompatible_reason == ""
    assert not diagnostic.missing_call_ids
    assert not diagnostic.unmatched_outputs
    assert tool_pair_boundary_is_safe(items, 0)
    assert sanitize_responses_body(projected.body).body == projected.body


def test_custom_tool_output_cannot_claim_function_delivery_provenance() -> None:
    delivery = {
        "type": "custom_tool_call_output",
        "id": "delivery",
        "name": "wait",
        "namespace": "functions",
        "output": "Task completed",
    }
    raw = json.dumps({"input": [delivery]}).encode()

    assert sanitize_responses_body(raw).body is None
    assert diagnose(raw).first_incompatible_reason
    assert not tool_pair_boundary_is_safe([delivery], 0)


@pytest.mark.parametrize("standalone_first", [False, True])
def test_standalone_and_paired_outputs_share_one_item_identity(standalone_first: bool) -> None:
    delivery = {
        "type": "function_call_output",
        "id": "delivery",
        "name": "wait",
        "namespace": "functions",
        "output": "Task completed",
    }
    pair = [
        {"type": "function_call", "call_id": "job", "name": "run", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "job", "id": "delivery", "output": "Other"},
    ]
    items = [delivery, *pair] if standalone_first else [*pair, delivery]
    raw = json.dumps({"input": items}).encode()

    assert sanitize_responses_body(raw).reason == "duplicate_output"
    assert diagnose(raw).duplicate_outputs
    assert not tool_pair_boundary_is_safe(items, 0)


def test_standalone_delivery_identity_cannot_be_repeated() -> None:
    delivery = {
        "type": "function_call_output",
        "id": "delivery",
        "name": "wait",
        "namespace": "functions",
        "output": "Task completed",
    }
    raw = json.dumps({"input": [delivery, delivery]}).encode()

    assert sanitize_responses_body(raw).reason == "duplicate_output"
    assert diagnose(raw).duplicate_outputs
    assert not tool_pair_boundary_is_safe([delivery, delivery], 0)
