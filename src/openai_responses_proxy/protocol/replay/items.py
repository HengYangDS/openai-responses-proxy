"""Own Responses input classification and ordered tool relationships."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from typing import Literal


class ProjectionStrategy(StrEnum):
    """Provider-portable disposition for one recognized item kind."""

    MESSAGE = "message"
    AGENT_MESSAGE = "agent_message"
    CALL = "call"
    OUTPUT = "output"
    TOOL_CATALOG = "tool_catalog"
    COMPACTION_TRIGGER = "compaction_trigger"
    DROP_REASONING = "drop_reasoning"
    DROP_REFERENCE = "drop_reference"
    DROP_SEARCH = "drop_search"
    DROP_AUXILIARY = "drop_auxiliary"
    DROP_LOCAL_TOOL = "drop_local_tool"
    SCHEMA_DRIFT = "schema_drift"


@dataclass(frozen=True, slots=True)
class ItemPolicy:
    """Classification facts shared by diagnostics and projection."""

    projection: ProjectionStrategy
    paired_output: str | None = None
    argument_field: str | None = None

    @property
    def rejection_reason(self) -> str:
        """Return the bounded local reason for a non-portable recognized item."""
        return "schema_drift" if self.projection is ProjectionStrategy.SCHEMA_DRIFT else ""


def _policy(
    projection: ProjectionStrategy,
    *,
    paired_output: str | None = None,
    argument_field: str | None = None,
) -> ItemPolicy:
    return ItemPolicy(projection, paired_output, argument_field)


# Client-visible item kinds are derived from the installed Codex protocol
# vocabulary. A recognized kind without a safe provider-portable projection is
# rejected as schema drift rather than mislabeled as an unknown future kind.
_SCHEMA_DRIFT_TYPES = (
    "apply_patch_call",
    "apply_patch_call_output",
    "code_interpreter_call",
    "computer_call",
    "computer_call_output",
    "file_search_call",
    "local_shell_call_output",
    "mcp_approval_request",
    "mcp_approval_response",
    "mcp_call",
    "mcp_list_tools",
    "program",
    "program_output",
    "shell_call",
    "shell_call_output",
)

ITEM_POLICIES: Final = MappingProxyType(
    {
        "message": _policy(ProjectionStrategy.MESSAGE),
        "agent_message": _policy(ProjectionStrategy.AGENT_MESSAGE),
        "additional_tools": _policy(ProjectionStrategy.TOOL_CATALOG),
        "reasoning": _policy(ProjectionStrategy.DROP_REASONING),
        "item_reference": _policy(ProjectionStrategy.DROP_REFERENCE),
        "compaction": _policy(ProjectionStrategy.DROP_REFERENCE),
        "context_compaction": _policy(ProjectionStrategy.DROP_REFERENCE),
        "web_search_call": _policy(ProjectionStrategy.DROP_SEARCH),
        "tool_search_call": _policy(
            ProjectionStrategy.DROP_SEARCH,
            paired_output="tool_search_output",
        ),
        "tool_search_output": _policy(ProjectionStrategy.DROP_SEARCH),
        "image_generation_call": _policy(ProjectionStrategy.DROP_AUXILIARY),
        "local_shell_call": _policy(
            ProjectionStrategy.DROP_LOCAL_TOOL,
            paired_output="function_call_output",
        ),
        "function_call": _policy(
            ProjectionStrategy.CALL,
            paired_output="function_call_output",
            argument_field="arguments",
        ),
        "function_call_output": _policy(ProjectionStrategy.OUTPUT),
        "custom_tool_call": _policy(
            ProjectionStrategy.CALL,
            paired_output="custom_tool_call_output",
            argument_field="input",
        ),
        "custom_tool_call_output": _policy(ProjectionStrategy.OUTPUT),
        "compaction_trigger": _policy(ProjectionStrategy.COMPACTION_TRIGGER),
    }
    | {item_type: _policy(ProjectionStrategy.SCHEMA_DRIFT) for item_type in _SCHEMA_DRIFT_TYPES}
)


def classify_item(item_type: object) -> ItemPolicy | None:
    """Return the policy for a recognized string item type."""
    return ITEM_POLICIES.get(item_type) if isinstance(item_type, str) else None


def is_current_turn_control(item: object) -> bool:
    """Keep active tool availability and compaction intent through recovery."""
    if not isinstance(item, dict):
        return False
    policy = classify_item(item.get("type"))
    return policy is not None and policy.projection in {
        ProjectionStrategy.TOOL_CATALOG,
        ProjectionStrategy.COMPACTION_TRIGGER,
    }


def item_types() -> frozenset[str]:
    """Return the recognized item kinds declared by the policy."""
    return frozenset(ITEM_POLICIES)


_PAIRED_OUTPUTS: Final = frozenset(
    policy.paired_output for policy in ITEM_POLICIES.values() if policy.paired_output is not None
)

type RelationshipIssue = Literal[
    "",
    "missing_call_id",
    "duplicate_call",
    "output_before_call",
    "mismatched_output_type",
    "duplicate_output",
]


@dataclass(slots=True)
class ToolRelationships:
    """Own ordered call identity and output matching for one request projection."""

    _calls: dict[str, str] = field(default_factory=dict)
    _call_names: dict[str, str] = field(default_factory=dict)
    _outputs: set[str] = field(default_factory=set)
    _output_ids: set[str] = field(default_factory=set)
    _delivery_ids: set[str] = field(default_factory=set)
    _flags: set[str] = field(default_factory=set)

    def observe(
        self, item_type: object, call_id: object, *, name: object = None, item_id: object = None
    ) -> RelationshipIssue:
        """Record one declared relationship and return its first structural issue."""
        if not isinstance(item_type, str):
            return ""
        policy = ITEM_POLICIES.get(item_type)
        if policy is None or (policy.paired_output is None and item_type not in _PAIRED_OUTPUTS):
            return ""
        if not isinstance(call_id, str) or not call_id:
            self._flags.add("missing_call_ids")
            return "missing_call_id"
        if policy.paired_output is not None:
            if call_id in self._calls:
                self._flags.add("duplicate_calls")
                return "duplicate_call"
            self._calls[call_id] = item_type
            if isinstance(name, str) and name:
                self._call_names[call_id] = name
            return ""
        duplicate = call_id in self._outputs
        self._outputs.add(call_id)
        call = self._calls.get(call_id)
        if call is None:
            self._flags.add("outputs_before_calls")
            return "output_before_call"
        if ITEM_POLICIES[call].paired_output != item_type:
            self._flags.add("mismatched_output_types")
            return "mismatched_output_type"
        if isinstance(item_id, str) and item_id in self._output_ids:
            self._flags.add("duplicate_outputs")
            return "duplicate_output"
        if duplicate and not self._admit_delivery(call_id, name, item_id):
            self._flags.add("duplicate_outputs")
            return "duplicate_output"
        if isinstance(item_id, str) and item_id:
            self._output_ids.add(item_id)
        self._flags.add("matched_pairs")
        return ""

    def _admit_delivery(self, call_id: str, name: object, item_id: object) -> bool:
        if (
            self._calls[call_id] in {"function_call", "custom_tool_call"}
            and isinstance(name, str)
            and name == self._call_names.get(call_id)
            and isinstance(item_id, str)
            and item_id
            and item_id not in self._output_ids
        ):
            self._delivery_ids.add(item_id)
            return True
        return False

    def is_delivery(self, item_id: object) -> bool:
        """Return whether this item is an admitted later result, not a second pair."""
        return isinstance(item_id, str) and item_id in self._delivery_ids

    def call_type(self, call_id: object) -> str | None:
        """Return the original declared call kind without accepting a new identity."""
        return self._calls.get(call_id) if isinstance(call_id, str) else None

    def has_pending_call(self, item_type: str) -> bool:
        """Return whether the given call kind still lacks an observed output."""
        return any(
            kind == item_type and call_id not in self._outputs
            for call_id, kind in self._calls.items()
        )

    @property
    def diagnostic_flags(self) -> frozenset[str]:
        """Return content-free facts, including currently unmatched identities."""
        flags = set(self._flags)
        if not self._calls.keys() <= self._outputs:
            flags.add("unmatched_calls")
        if not self._outputs <= self._calls.keys():
            flags.add("unmatched_outputs")
        return frozenset(flags)


def call_argument_fields() -> dict[str, str]:
    """Return call argument fields declared by the policy."""
    return {
        item_type: policy.argument_field
        for item_type, policy in ITEM_POLICIES.items()
        if policy.argument_field is not None
    }
