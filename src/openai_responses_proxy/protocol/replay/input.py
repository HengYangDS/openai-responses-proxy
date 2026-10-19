"""Project an input sequence with one replay grammar and tool-relationship owner.

Item adapters normalize portable messages, calls, deliveries and native controls.
The sequence projection preserves their dependencies for bounded recovery.
"""

from __future__ import annotations

import json
from typing import cast

from openai_responses_proxy.protocol.replay import items as item_policy
from openai_responses_proxy.protocol.replay.content import empty_assistant_placeholder
from openai_responses_proxy.protocol.replay.content import project_assistant_text
from openai_responses_proxy.protocol.replay.content import project_input_content
from openai_responses_proxy.protocol.replay.content import reject as _reject

type JsonObject = dict[str, object]


EMPTY_TOOL_OUTPUT_MARKER = "[tool returned no textual output]"


_VALID_ROLES = frozenset(("user", "assistant", "developer", "system"))


_VALID_PHASES = frozenset(("commentary", "final_answer"))


_LOCAL_SHELL_STATUSES = frozenset(("completed", "in_progress", "incomplete"))


_LOCAL_SHELL_TIMEOUT_MAX = 2**64 - 1


_LOCAL_SHELL_ACTION_FIELDS = frozenset(
    ("type", "command", "timeout_ms", "working_directory", "env", "user")
)


_CALL_ARGUMENT_FIELD = item_policy.call_argument_fields()


_MESSAGE_FIELDS = frozenset(
    (
        "type",
        "id",
        "status",
        "role",
        "content",
        "phase",
        "name",
        "internal_chat_message_metadata_passthrough",
    )
)


_AGENT_FIELDS = frozenset(
    (
        "type",
        "id",
        "status",
        "author",
        "recipient",
        "phase",
        "content",
        "encrypted_content",
        "internal_chat_message_metadata_passthrough",
    )
)


_CALL_FIELDS = {
    item_type: frozenset(
        (
            "type",
            "id",
            "status",
            "call_id",
            "name",
            argument_field,
            "namespace",
            "caller",
            "internal_chat_message_metadata_passthrough",
        )
    )
    for item_type, argument_field in _CALL_ARGUMENT_FIELD.items()
}
_CALL_FIELDS["function_call"] = _CALL_FIELDS["function_call"] | {"encrypted_function_args"}


_OUTPUT_FIELDS = frozenset(
    (
        "type",
        "id",
        "status",
        "call_id",
        "name",
        "output",
        "caller",
        "encrypted_content",
        "internal_chat_message_metadata_passthrough",
    )
)


_FUNCTION_OUTPUT_FIELDS = _OUTPUT_FIELDS | {"namespace"}


_TOOL_CATALOG_FIELDS = frozenset(("type", "id", "role", "tools"))


def _unknown_fields(item: JsonObject, allowed: frozenset[str], reason: str) -> None:
    if set(item) - allowed:
        _reject(reason)


def _valid_caller(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    caller_type: object = value.get("type")
    if caller_type == "direct":
        return set(value) == {"type"}
    caller_id: object = value.get("caller_id")
    return (
        caller_type == "program"
        and set(value) == {"type", "caller_id"}
        and isinstance(caller_id, str)
        and bool(caller_id)
    )


def _project_message(item: JsonObject) -> tuple[JsonObject | None, dict[str, int]]:
    _unknown_fields(item, _MESSAGE_FIELDS, "unknown_message_field")
    role = item.get("role")
    phase = item.get("phase")
    if not isinstance(role, str) or role not in _VALID_ROLES:
        _reject("invalid_message_role")
    if phase is not None and (
        role != "assistant" or not isinstance(phase, str) or phase not in _VALID_PHASES
    ):
        _reject("invalid_message_phase")
    if role == "assistant":
        if empty_assistant_placeholder(item.get("content")):
            return None, {
                "changed": 1,
                "item_ids": int("id" in item),
                "encrypted_blocks": 0,
                "omission_markers": 0,
                "local_image_items": 0,
            }
        content, changed, encrypted, markers = project_assistant_text(
            item.get("content"), encrypted_marker=False
        )
        local_images = 0
    else:
        content, changed, encrypted, markers, local_images = project_input_content(
            item.get("content"), allow_images=True, encrypted_marker=False
        )
    projected: JsonObject = {"type": "message", "role": role, "content": content}
    if phase is not None:
        projected["phase"] = phase
    return projected, {
        "changed": int(changed or projected != item),
        "item_ids": int("id" in item),
        "encrypted_blocks": encrypted,
        "omission_markers": markers,
        "local_image_items": local_images,
    }


def _agent_ciphertext(item: JsonObject) -> int:
    ciphertext = []
    if "encrypted_content" in item:
        ciphertext.append(item["encrypted_content"])
    blocks = item.get("content")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "encrypted_content":
                if set(block) != {"type", "encrypted_content"}:
                    _reject("invalid_encrypted_content")
                ciphertext.append(block["encrypted_content"])
    if any(not isinstance(value, str) or not value for value in ciphertext):
        _reject("invalid_encrypted_content")
    return len(ciphertext)


def _project_agent_message(item: JsonObject) -> tuple[JsonObject, dict[str, int]]:
    _unknown_fields(item, _AGENT_FIELDS, "unknown_agent_message_field")
    author, recipient = item.get("author"), item.get("recipient")
    phase = item.get("phase", "commentary")
    if not all(isinstance(value, str) and value for value in (author, recipient)):
        _reject("invalid_agent_message")
    if not isinstance(phase, str) or phase not in _VALID_PHASES:
        _reject("invalid_agent_phase")
    ciphertext = _agent_ciphertext(item)
    root_ciphertext = int("encrypted_content" in item)
    content, _changed, encrypted, markers = project_assistant_text(
        item.get("content"), encrypted_marker=True, root_ciphertext=root_ciphertext
    )
    if ciphertext:
        native = {
            key: value
            for key, value in item.items()
            if key not in {"internal_chat_message_metadata_passthrough", "status"}
        }
        return native, {"changed": int(native != item)}
    header = json.dumps(
        {"type": "agent_message", "author": author, "recipient": recipient},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "message",
        "role": "assistant",
        "phase": phase,
        "content": header + "\n" + content,
    }, {
        "changed": 1,
        "item_ids": int("id" in item),
        "encrypted_blocks": encrypted,
        "omission_markers": markers,
        "local_image_items": 0,
    }


def _project_call(
    item: JsonObject, relationships: item_policy.ToolRelationships
) -> tuple[JsonObject, dict[str, int]]:
    item_type = cast(str, item.get("type"))
    _unknown_fields(item, _CALL_FIELDS[item_type], "unknown_call_field")
    call_id, name = item.get("call_id"), item.get("name")
    argument_field = _CALL_ARGUMENT_FIELD[item_type]
    argument = item.get(argument_field)
    namespace, caller = item.get("namespace"), item.get("caller")
    if relationships.observe_item(item):
        _reject("invalid_call_id")
    valid_call_id = cast(str, call_id)
    if not isinstance(name, str) or not name or not isinstance(argument, str):
        _reject("invalid_call")
    if namespace is not None and (not isinstance(namespace, str) or not namespace):
        _reject("invalid_namespace")
    if not _valid_caller(caller):
        _reject("invalid_caller")
    encrypted_args = item.get("encrypted_function_args")
    if "encrypted_function_args" in item and (
        not isinstance(encrypted_args, list)
        or any(not isinstance(value, str) or not value for value in encrypted_args)
    ):
        _reject("invalid_encrypted_function_args")
    projected: JsonObject = {
        "type": item_type,
        "call_id": valid_call_id,
        "name": name,
        argument_field: argument,
    }
    if namespace is not None:
        projected["namespace"] = namespace
    if caller is not None:
        projected["caller"] = caller
    if "encrypted_function_args" in item:
        projected["encrypted_function_args"] = encrypted_args
    return projected, {
        "changed": int(projected != item),
        "item_ids": int("id" in item),
        "encrypted_blocks": 0,
        "omission_markers": 0,
        "local_image_items": 0,
    }


def _drop_local_tool_call(
    item: JsonObject, relationships: item_policy.ToolRelationships
) -> dict[str, int]:
    allowed_fields = frozenset(
        (
            "type",
            "id",
            "call_id",
            "status",
            "action",
            "internal_chat_message_metadata_passthrough",
        )
    )
    _unknown_fields(item, allowed_fields, "unknown_local_shell_call_field")
    status, action = item.get("status"), item.get("action")
    if relationships.observe_item(item):
        _reject("invalid_call_id")
    if (
        not isinstance(status, str)
        or status not in _LOCAL_SHELL_STATUSES
        or not isinstance(action, dict)
    ):
        _reject("invalid_local_shell_call")
    if set(action) - _LOCAL_SHELL_ACTION_FIELDS or action.get("type") != "exec":
        _reject("invalid_local_shell_call")
    commands = action.get("command")
    if (
        not isinstance(commands, list)
        or not commands
        or not all(isinstance(command, str) and command for command in commands)
    ):
        _reject("invalid_local_shell_call")
    timeout = action.get("timeout_ms")
    if timeout is not None and (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 0 <= timeout <= _LOCAL_SHELL_TIMEOUT_MAX
    ):
        _reject("invalid_local_shell_call")
    for field in ("working_directory", "user"):
        value = action.get(field)
        if value is not None and not isinstance(value, str):
            _reject("invalid_local_shell_call")
    environment = action.get("env")
    if environment is not None and (
        not isinstance(environment, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()
        )
    ):
        _reject("invalid_local_shell_call")
    return {
        "changed": 1,
        "item_ids": int("id" in item),
        "encrypted_blocks": 0,
        "omission_markers": 0,
        "local_image_items": 0,
    }


def _project_output(
    item: JsonObject,
    relationships: item_policy.ToolRelationships,
) -> tuple[JsonObject, dict[str, int]]:
    item_type = cast(str, item.get("type"))
    allowed_fields = (
        _FUNCTION_OUTPUT_FIELDS if item_type == "function_call_output" else _OUTPUT_FIELDS
    )
    _unknown_fields(item, allowed_fields, "unknown_output_field")
    call_id, caller = item.get("call_id"), item.get("caller")
    issue = relationships.observe_item(item)
    if issue:
        _reject(
            {
                "missing_call_id": "orphan_output",
                "output_before_call": "orphan_output",
                "mismatched_output_type": "mismatched_output",
            }.get(issue, issue)
        )
    valid_call_id = cast(str, call_id)
    if not _valid_caller(caller):
        _reject("invalid_caller")
    raw_output = item.get("output")
    root_value = item.get("encrypted_content")
    if "encrypted_content" in item and not isinstance(root_value, str):
        _reject("invalid_encrypted_content")
    root_ciphertext = int(isinstance(root_value, str))
    empty_tool_outputs = int(raw_output == "" and not root_ciphertext)
    if empty_tool_outputs:
        output, changed, encrypted, markers, local_images = (
            EMPTY_TOOL_OUTPUT_MARKER,
            True,
            0,
            0,
            0,
        )
    else:
        output, changed, encrypted, markers, local_images = project_input_content(
            raw_output,
            allow_images=True,
            encrypted_marker=True,
            root_ciphertext=root_ciphertext,
        )
    projected: JsonObject = {
        "type": item_type,
        "call_id": valid_call_id,
        "output": output,
    }
    if caller is not None:
        projected["caller"] = caller
    if relationships.is_delivery(item.get("id")):
        text, _changed, _encrypted, _markers = project_assistant_text(
            output, encrypted_marker=False
        )
        header: JsonObject = {
            "type": "tool_delivery",
            "call_id": valid_call_id,
            "name": item["name"],
        }
        if caller is not None:
            header["caller"] = caller
        projected = _delivery_message(header, text)
    return projected, {
        "changed": int(changed or projected != item),
        "item_ids": int("id" in item),
        "encrypted_blocks": encrypted,
        "omission_markers": markers,
        "local_image_items": local_images,
        "empty_tool_outputs": empty_tool_outputs,
    }


def _delivery_message(header: JsonObject, output: str) -> JsonObject:
    return {
        "type": "message",
        "role": "assistant",
        "phase": "commentary",
        "content": json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n" + output,
    }


def _project_detached_delivery(
    item: JsonObject, relationships: item_policy.ToolRelationships
) -> tuple[JsonObject, dict[str, int]]:
    if issue := relationships.observe_item(item):
        _reject(issue)
    output, _changed, encrypted, markers = project_assistant_text(
        item.get("output"), encrypted_marker=False
    )
    return _delivery_message(
        {"type": "tool_delivery", "name": item["name"], "namespace": item["namespace"]}, output
    ), {
        "changed": 1,
        "item_ids": 1,
        "encrypted_blocks": encrypted,
        "omission_markers": markers,
        "local_image_items": 0,
        "empty_tool_outputs": 0,
    }


def _project_compaction_trigger(item: JsonObject) -> tuple[JsonObject, dict[str, int]]:
    _unknown_fields(item, frozenset(("type",)), "unknown_compaction_trigger_field")
    return {"type": "compaction_trigger"}, {
        "changed": 0,
        "item_ids": 0,
        "encrypted_blocks": 0,
        "omission_markers": 0,
        "local_image_items": 0,
    }


def _project_tool_catalog(item: JsonObject) -> tuple[JsonObject, dict[str, int]]:
    """Preserve the current turn's declared tools, not replay history."""
    _unknown_fields(item, _TOOL_CATALOG_FIELDS, "unknown_additional_tools_field")
    role, tools = item.get("role"), item.get("tools")
    item_id = item.get("id")
    if (
        not isinstance(role, str)
        or not role
        or not isinstance(tools, list)
        or any(not isinstance(tool, dict) or not tool for tool in tools)
        or ("id" in item and (not isinstance(item_id, str) or not item_id))
    ):
        _reject("invalid_additional_tools")
    return dict(item), {
        "changed": 0,
        "item_ids": 0,
        "encrypted_blocks": 0,
        "omission_markers": 0,
        "local_image_items": 0,
    }


def project_input(
    items: list[object],
) -> tuple[list[object], dict[str, int], item_policy.InputDependencies]:
    """Normalize one sequence while preserving exact tool and delivery dependencies."""
    relationships = item_policy.ToolRelationships()
    projected: list[object] = []
    metrics = {
        "reasoning_items": 0,
        "reference_items": 0,
        "search_items": 0,
        "item_ids": 0,
        "encrypted_blocks": 0,
        "omission_markers": 0,
        "local_image_items": 0,
        "empty_tool_outputs": 0,
        "changed_items": 0,
    }
    for raw_item in items:
        if not isinstance(raw_item, dict):
            _reject("invalid_item")
        item = cast(JsonObject, raw_item)
        policy = item_policy.classify_input_item(item)
        if policy is None:
            _reject("unknown_item_type")
        strategy = policy.projection
        if strategy in {
            item_policy.ProjectionStrategy.DROP_REASONING,
            item_policy.ProjectionStrategy.DROP_REFERENCE,
            item_policy.ProjectionStrategy.DROP_SEARCH,
            item_policy.ProjectionStrategy.DROP_AUXILIARY,
        }:
            if issue := relationships.observe_item(item):
                _reject(issue)
            metrics["reasoning_items"] += int(
                strategy is item_policy.ProjectionStrategy.DROP_REASONING
            )
            metrics["reference_items"] += int(
                strategy is item_policy.ProjectionStrategy.DROP_REFERENCE
            )
            metrics["search_items"] += int(strategy is item_policy.ProjectionStrategy.DROP_SEARCH)
            metrics["item_ids"] += int("id" in item)
            continue
        if strategy is item_policy.ProjectionStrategy.DROP_LOCAL_TOOL:
            item_metrics = _drop_local_tool_call(item, relationships)
            projection = None, item_metrics
        elif strategy is item_policy.ProjectionStrategy.MESSAGE:
            projection = _project_message(item)
        elif strategy is item_policy.ProjectionStrategy.AGENT_MESSAGE:
            projection = _project_agent_message(item)
        elif strategy is item_policy.ProjectionStrategy.CALL:
            projection = _project_call(item, relationships)
        elif (
            strategy is item_policy.ProjectionStrategy.OUTPUT
            and relationships.call_type(item.get("call_id")) == "local_shell_call"
        ):
            _value, item_metrics = _project_output(item, relationships)
            item_metrics["changed"] = 1
            projection = None, item_metrics
        elif strategy is item_policy.ProjectionStrategy.DETACHED_DELIVERY:
            projection = _project_detached_delivery(item, relationships)
        elif strategy is item_policy.ProjectionStrategy.OUTPUT:
            projection = _project_output(item, relationships)
        elif strategy is item_policy.ProjectionStrategy.COMPACTION_TRIGGER:
            projection = _project_compaction_trigger(item)
        elif strategy is item_policy.ProjectionStrategy.TOOL_CATALOG:
            projection = _project_tool_catalog(item)
        else:
            _reject(policy.rejection_reason)
        value, item_metrics = projection
        if value is not None:
            relationships.record_projection(item, len(projected))
            projected.append(value)
        for key, value_count in item_metrics.items():
            metrics["changed_items" if key == "changed" else key] += value_count
    if relationships.has_pending_call("local_shell_call"):
        _reject("incomplete_local_shell_pair")
    if not projected and items:
        _reject("empty_portable_input")
    return projected, metrics, relationships.input_dependencies
