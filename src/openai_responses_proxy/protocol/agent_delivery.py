"""Select readable delegation before generation and restore native delivery metadata.

The plaintext policy aliases the model's reserved collaboration schema upstream.
Only a response to that selected schema can enter Codex's plaintext delivery
path. Existing encrypted messages and arbitrary response text remain untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from typing import cast

from openai_responses_proxy.protocol.response import ResponseValidationError
from openai_responses_proxy.protocol.response import ValidationReason
from openai_responses_proxy.protocol.response import sse_event_data

type DeliveryMode = Literal["native", "plaintext"]
type JsonObject = dict[str, object]

NATIVE_NAMESPACE = "collaboration"
PLAINTEXT_NAMESPACE = "collaboration_plaintext"
_MESSAGE_TOOLS = frozenset(("spawn_agent", "send_message", "followup_task"))
_CALL_TYPES = frozenset(("function_call", "custom_tool_call"))


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """Request-local schema selection and its response restoration obligation."""

    body: bytes
    active: bool = False


def _object(raw: bytes, reason: str) -> JsonObject:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(reason) from error
    if not isinstance(value, dict):
        raise ValueError(reason)
    return cast(JsonObject, value)


def _catalogs(payload: JsonObject) -> list[list[object]]:
    catalogs: list[list[object]] = []
    if "tools" in payload:
        tools = payload["tools"]
        if not isinstance(tools, list):
            raise ValueError("invalid_agent_delivery_request")
        catalogs.append(tools)
    items = payload.get("input")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                tools = item.get("tools")
                if not isinstance(tools, list):
                    raise ValueError("invalid_agent_delivery_request")
                catalogs.append(tools)
    return catalogs


def _select_catalog(tools: list[object]) -> bool:
    active = False
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "namespace":
            continue
        name = tool.get("name")
        if name == PLAINTEXT_NAMESPACE:
            raise ValueError("agent_delivery_namespace_conflict")
        if name != NATIVE_NAMESPACE:
            continue
        children = tool.get("tools")
        if not isinstance(children, list):
            raise ValueError("invalid_agent_delivery_request")
        tool["name"] = PLAINTEXT_NAMESPACE
        active = True
        for child in children:
            if not isinstance(child, dict) or child.get("name") not in _MESSAGE_TOOLS:
                continue
            parameters = child.get("parameters")
            properties = parameters.get("properties") if isinstance(parameters, dict) else None
            message = properties.get("message") if isinstance(properties, dict) else None
            if not isinstance(message, dict) or message.get("type") != "string":
                raise ValueError("invalid_agent_delivery_request")
            message.pop("encrypted", None)
    return active


def _select_replayed_call(item: JsonObject) -> bool:
    if item.get("type") not in _CALL_TYPES or item.get("namespace") != NATIVE_NAMESPACE:
        return False
    item["namespace"] = PLAINTEXT_NAMESPACE
    if item.get("encrypted_function_args") == []:
        item.pop("encrypted_function_args")
    return True


def prepare_request(raw: bytes, mode: DeliveryMode) -> PreparedRequest:
    """Select the released route's delegation policy without reading client state."""
    if mode == "native":
        return PreparedRequest(raw)
    payload = _object(raw, "invalid_agent_delivery_request")
    active = False
    for catalog in _catalogs(payload):
        active = _select_catalog(catalog) or active
    items = payload.get("input")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                active = _select_replayed_call(item) or active
    if not active:
        return PreparedRequest(raw)
    choice = payload.get("tool_choice")
    if isinstance(choice, dict) and choice.get("namespace") == NATIVE_NAMESPACE:
        choice["namespace"] = PLAINTEXT_NAMESPACE
    return PreparedRequest(_encode(payload), True)


def _encode(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid_agent_delivery_payload") from error


def _restore_item(item: JsonObject, *, complete: bool) -> bool:
    if item.get("type") not in _CALL_TYPES or item.get("namespace") != PLAINTEXT_NAMESPACE:
        return False
    if item.get("name") in _MESSAGE_TOOLS and (
        complete or item.get("status") != "in_progress" or item.get("arguments")
    ):
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            raise ResponseValidationError(ValidationReason.UNPROVED_AGENT_PLAINTEXT)
        try:
            parsed = json.loads(arguments)
        except (ValueError, RecursionError) as error:
            raise ResponseValidationError(ValidationReason.UNPROVED_AGENT_PLAINTEXT) from error
        message = parsed.get("message") if isinstance(parsed, dict) else None
        encrypted = item.get("encrypted_function_args")
        if (
            not isinstance(message, str)
            or not message
            or message.lstrip().startswith("gAAAA")
            or encrypted not in (None, [])
        ):
            raise ResponseValidationError(ValidationReason.UNPROVED_AGENT_PLAINTEXT)
        item["encrypted_function_args"] = []
    item["namespace"] = NATIVE_NAMESPACE
    return True


def _restore_payload(payload: JsonObject, *, streaming: bool) -> bool:
    changed = False
    item = payload.get("item")
    if isinstance(item, dict):
        complete = not streaming or payload.get("type") != "response.output_item.added"
        changed = _restore_item(item, complete=complete)
    response = payload.get("response")
    terminal = response if isinstance(response, dict) else payload
    output = terminal.get("output")
    complete = not streaming or payload.get("type") not in (
        "response.created",
        "response.in_progress",
    )
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                changed = _restore_item(item, complete=complete) or changed
    return changed


def restore_response(raw: bytes, active: bool) -> bytes:
    """Restore a selected JSON response; preserve all content and call identities."""
    if not active:
        return raw
    payload = _object(raw, "invalid_agent_delivery_payload")
    return _encode(payload) if _restore_payload(payload, streaming=False) else raw


def restore_event(raw: bytes, active: bool) -> bytes:
    """Restore one complete SSE event while retaining its non-data fields."""
    if not active:
        return raw
    payload = sse_event_data(raw)
    if not isinstance(payload, dict) or not _restore_payload(payload, streaming=True):
        return raw
    lines = raw.splitlines(keepends=True)
    output: list[bytes] = []
    replaced = False
    for line in lines:
        if line.partition(b":")[0] != b"data":
            output.append(line)
        elif not replaced:
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
            output.append(b"data: " + _encode(payload) + ending)
            replaced = True
    return b"".join(output)
