"""Bounded compatibility policy for Responses ``input`` validation failures.

This module owns the only recovery admitted for the observed third-party
``validation_error`` contract.  It is pure request policy: no network, process,
logging, metrics, or conversation storage access belongs here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from contextlib import suppress
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Final
from typing import cast

from openai_responses_proxy.protocol.replay import items as item_policy
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body

type JsonObject = dict[str, object]
type ReadOnlyJsonObject = Mapping[str, object]

_ERROR_MESSAGE: Final = (
    "invalid request body: Invalid 'input': value did not match any expected variant"
)
_INVALID_JSON: Final = object()
_KNOWN_ITEM_TYPES: Final = item_policy.item_types()
_KNOWN_CONTENT_TYPES: Final = frozenset(
    [
        "input_text",
        "output_text",
        "input_image",
        "input_file",
        "input_audio",
        "refusal",
        "encrypted_content",
    ]
)
_ROLES: Final = ("system", "developer", "user")
_VALUE_KINDS: Final = {
    type(None): "null",
    bool: "bool",
    str: "str",
    int: "number",
    float: "number",
    list: "list",
    dict: "dict",
}
_SIZE_BUCKETS: Final = ((0, "0"), (1, "1"), (4, "2-4"), (16, "5-16"))
_TEXT_CONTENT_TYPES: Final = frozenset(("input_text", "output_text"))
_BUCKETS: Final = frozenset(("0", "1", "2-4", "5-16", "17+"))


@dataclass(frozen=True, slots=True)
class InputDiagnostic:
    """Content-free structural facts about one rejected Responses input."""

    input_items_bucket: str = "0"
    item_types: dict[str, str] = field(default_factory=dict)
    content_types: dict[str, str] = field(default_factory=dict)
    matched_pairs: bool = False
    unmatched_calls: bool = False
    unmatched_outputs: bool = False
    outputs_before_calls: bool = False
    duplicate_calls: bool = False
    duplicate_outputs: bool = False
    missing_call_ids: bool = False
    mismatched_output_types: bool = False
    first_incompatible_reason: str = ""
    shape_sha256: str = "invalid-json"


@dataclass(frozen=True, slots=True)
class RecoveryMetrics:
    """Non-content facts proving that a recovery request is materially smaller."""

    original_bytes: int
    recovery_bytes: int
    retained_messages: int
    dropped_input_items: int
    prompt_cache_key_removed: bool


def is_exact_validation_error(status_code: int, error_body: bytes) -> bool:
    """Return whether an upstream response is the complete observed error contract."""
    payload = _load_json(error_body)
    error = payload.get("error") if isinstance(payload, dict) else None
    expected: object = {
        "message": _ERROR_MESSAGE,
        "type": "invalid_request_error",
        "param": "",
        "code": "validation_error",
    }
    return status_code == 400 and bool(error == expected)


def diagnose(raw: bytes) -> InputDiagnostic:
    """Observe raw structure and report the request owner's projection verdict."""
    payload = _load_json(raw)
    reason = sanitize_responses_body(raw).reason or ""
    if payload is _INVALID_JSON:
        return InputDiagnostic(first_incompatible_reason=reason)
    shape_sha256 = hashlib.sha256(
        json.dumps(_shape(payload), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    items = payload.get("input") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return InputDiagnostic(first_incompatible_reason=reason, shape_sha256=shape_sha256)
    state = _DiagnosticState(shape_sha256=shape_sha256, input_items=len(items), first_reason=reason)
    for item in items:
        state.observe(item)
    return state.finish()


def _load_json(raw: bytes) -> object:
    payload: object = _INVALID_JSON
    with suppress(TypeError, ValueError, json.JSONDecodeError):
        payload = json.loads(raw)
    return payload


def diagnostic_dict(diagnostic: InputDiagnostic) -> dict[str, object]:
    """Project an immutable diagnostic to the stable runtime mapping contract."""
    return {
        "input_items_bucket": diagnostic.input_items_bucket,
        "item_types": diagnostic.item_types,
        "content_types": diagnostic.content_types,
        "matched_pairs": diagnostic.matched_pairs,
        "unmatched_calls": diagnostic.unmatched_calls,
        "unmatched_outputs": diagnostic.unmatched_outputs,
        "outputs_before_calls": diagnostic.outputs_before_calls,
        "duplicate_calls": diagnostic.duplicate_calls,
        "duplicate_outputs": diagnostic.duplicate_outputs,
        "missing_call_ids": diagnostic.missing_call_ids,
        "mismatched_output_types": diagnostic.mismatched_output_types,
        "first_incompatible_reason": diagnostic.first_incompatible_reason,
        "shape_sha256": diagnostic.shape_sha256,
    }


def format_diagnostic(diagnostic: InputDiagnostic | dict[str, object]) -> str:
    """Render a bounded, content-free diagnostic fragment."""
    values = asdict(diagnostic) if isinstance(diagnostic, InputDiagnostic) else diagnostic
    fields: dict[str, object] = {
        "input_items_bucket": _size_bucket_value(values.get("input_items_bucket")),
        "item_types": _format_counts(_bucket_counts(values.get("item_types"))),
        "content_types": _format_counts(_bucket_counts(values.get("content_types"))),
        **{key: _presence(values.get(key) is True) for key in _DIAGNOSTIC_FLAGS},
        "first_incompatible_reason": str(values.get("first_incompatible_reason", "")) or "-",
        "shape_sha256": str(values.get("shape_sha256", "-")),
    }
    return " ".join(f"{key}={value}" for key, value in fields.items())[:768]


def build_recovery(raw: bytes, budget: int) -> tuple[bytes | None, RecoveryMetrics | None]:
    """Keep current dialogue and turn controls in one smaller request."""
    if isinstance(budget, bool) or budget <= 0:
        return None, None
    payload = _load_json(raw)
    if not isinstance(payload, dict):
        return None, None
    original_items = payload.get("input")
    if not isinstance(original_items, list) or not original_items:
        return None, None
    dialogue = _current_dialogue(original_items)
    if dialogue is None:
        return None, None
    candidate = dict(payload)
    candidate["input"] = dialogue
    candidate["store"] = False
    removed_bindings = sum(key in candidate for key in _PROVIDER_BINDINGS)
    for key in _PROVIDER_BINDINGS:
        candidate.pop(key, None)
    include_removed = _remove_reasoning_include(candidate)
    try:
        recovery = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None, None
    dropped_items = len(original_items) - len(dialogue)
    materially_changed = dropped_items > 0 or removed_bindings > 0 or include_removed
    if not materially_changed or len(recovery) > budget or len(recovery) >= len(raw):
        return None, None
    return recovery, RecoveryMetrics(
        original_bytes=len(raw),
        recovery_bytes=len(recovery),
        retained_messages=sum(item.get("type") == "message" for item in dialogue),
        dropped_input_items=dropped_items,
        prompt_cache_key_removed="prompt_cache_key" in payload,
    )


_PROVIDER_BINDINGS: Final = ("previous_response_id", "conversation", "prompt_cache_key")
_DIAGNOSTIC_FLAGS: Final = (
    "matched_pairs",
    "unmatched_calls",
    "unmatched_outputs",
    "outputs_before_calls",
    "duplicate_calls",
    "duplicate_outputs",
    "missing_call_ids",
    "mismatched_output_types",
)


def _current_dialogue(items: Sequence[object]) -> list[JsonObject] | None:
    latest: dict[str, int] = {}
    for index, item in enumerate(items):
        match item:
            case {"type": "message", "role": str(role)} if role in _ROLES:
                latest[role] = index
    if "user" not in latest:
        return None
    retained = set(latest.values())
    retained.update(
        index for index, item in enumerate(items) if item_policy.is_current_turn_control(item)
    )
    dialogue: list[JsonObject] = []
    for index in sorted(retained):
        item = items[index]
        if item_policy.is_current_turn_control(item):
            dialogue.append(cast(JsonObject, item))
            continue
        match item:
            case {"role": str(role), "content": value} if content := _text_content(value):
                dialogue.append({"type": "message", "role": role, "content": content})
            case _:
                return None
    return dialogue


def _text_content(value: object) -> str | list[JsonObject] | None:
    match value:
        case str() if value:
            return value
        case list():
            projected: list[JsonObject] = [
                {"type": "input_text", "text": text}
                for block in value
                if isinstance(block, dict)
                and block.get("type") in _TEXT_CONTENT_TYPES
                and isinstance((text := block.get("text")), str)
                and text
            ]
            return projected or None
        case _:
            return None


def _remove_reasoning_include(candidate: JsonObject) -> bool:
    include = candidate.get("include")
    if not isinstance(include, list):
        return False
    filtered = [value for value in include if value != "reasoning.encrypted_content"]
    candidate["include"] = filtered
    return len(filtered) != len(include)


def _shape(value: object, depth: int = 0) -> object:
    """Return a capped categorical shape; cardinalities and unknown names are erased."""
    if depth >= 6:
        return "truncated", _value_kind(value)
    if isinstance(value, list):
        items = {_shape(item, depth + 1) for item in value}
        return "list", _size_bucket(len(value)), _bounded(items)
    if not isinstance(value, dict):
        return _value_kind(value)
    entries: set[object] = set()
    for key, item in value.items():
        if key == "type":
            entry = (
                "type",
                _closed_label(item, _KNOWN_ITEM_TYPES | _KNOWN_CONTENT_TYPES),
            )
        elif key == "role":
            entry = "role", item if item in (*_ROLES, "assistant") else "unknown"
        else:
            entry = "field", _shape(item, depth + 1)
        entries.add(entry)
    known_fields = len(value.keys() & {"type", "role"})
    return (
        "dict",
        _size_bucket(known_fields),
        _presence(len(value) > known_fields),
        _bounded(entries),
    )


def _bounded(entries: AbstractSet[object]) -> tuple[tuple[object, ...], str]:
    ordered = tuple(sorted(entries, key=repr))
    return ordered[:32], _presence(len(ordered) > 32)


def _closed_label(value: object, known: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in known else _unknown_label(value)


def _unknown_label(value: object) -> str:
    return "unknown" if isinstance(value, str) else _value_kind(value)


def _value_kind(value: object) -> str:
    return _VALUE_KINDS.get(type(value), "other")


def _size_bucket(size: int) -> str:
    return next((label for limit, label in _SIZE_BUCKETS if size <= limit), "17+")


def _bucketed_counts(values: dict[str, int]) -> dict[str, str]:
    return {key: _size_bucket(count) for key, count in values.items()}


def _format_counts(values: dict[str, str]) -> str:
    return ",".join(f"{key}:{values[key]}" for key in sorted(values)) or "-"


def _presence(value: bool) -> str:
    return "present" if value else "absent"


class _DiagnosticState:
    """Collect raw structural facts without reinterpreting projection validity."""

    def __init__(self, *, shape_sha256: str, input_items: int, first_reason: str) -> None:
        self.shape_sha256 = shape_sha256
        self.input_items = input_items
        self.item_types: Counter[str] = Counter()
        self.content_types: Counter[str] = Counter()
        self.relationships = item_policy.ToolRelationships()
        self.first_reason = first_reason

    def observe(self, item: object) -> None:
        if not isinstance(item, dict):
            self.item_types[_value_kind(item)] += 1
            return
        typed_item = cast(ReadOnlyJsonObject, item)
        raw_type = typed_item.get("type")
        self.item_types[_closed_label(raw_type, _KNOWN_ITEM_TYPES)] += 1
        self._observe_content(typed_item.get("content"))
        self.relationships.observe(
            raw_type,
            typed_item.get("call_id"),
            name=typed_item.get("name"),
            item_id=typed_item.get("id"),
        )

    def _observe_content(self, content: object) -> None:
        if isinstance(content, str):
            self.content_types["string"] += 1
        elif isinstance(content, list):
            for block in content:
                self._observe_block(block)

    def _observe_block(self, block: object) -> None:
        if not isinstance(block, dict):
            self.content_types[_value_kind(block)] += 1
            return
        typed_block = cast(ReadOnlyJsonObject, block)
        block_type = typed_block.get("type")
        self.content_types[_closed_label(block_type, _KNOWN_CONTENT_TYPES)] += 1

    def finish(self) -> InputDiagnostic:
        flags = self.relationships.diagnostic_flags
        return InputDiagnostic(
            _size_bucket(self.input_items),
            _bucketed_counts(self.item_types),
            _bucketed_counts(self.content_types),
            *(flag in flags for flag in _DIAGNOSTIC_FLAGS),
            first_incompatible_reason=self.first_reason,
            shape_sha256=self.shape_sha256,
        )


def _bucket_counts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: cast(str, bucket)
        for key, bucket in value.items()
        if isinstance(key, str) and bucket in _BUCKETS
    }


def _size_bucket_value(value: object) -> str:
    return cast(str, value) if value in _BUCKETS else "0"
