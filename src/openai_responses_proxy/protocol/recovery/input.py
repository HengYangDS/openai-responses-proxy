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
from collections.abc import Set as AbstractSet
from contextlib import suppress
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
    with suppress(TypeError, ValueError, RecursionError, UnicodeError):
        payload = json.loads(raw)
    return payload


def format_diagnostic(diagnostic: InputDiagnostic) -> str:
    """Render a bounded, content-free diagnostic fragment."""
    fields = {
        "input_items_bucket": diagnostic.input_items_bucket,
        "item_types": _format_counts(diagnostic.item_types),
        "content_types": _format_counts(diagnostic.content_types),
        **{key: _presence(getattr(diagnostic, key)) for key in _DIAGNOSTIC_FLAGS},
        "first_incompatible_reason": diagnostic.first_incompatible_reason or "-",
        "shape_sha256": diagnostic.shape_sha256,
    }
    return " ".join(f"{key}={value}" for key, value in fields.items())[:768]


def build_recovery(
    raw: bytes,
    budget: int,
    *,
    input_dependencies: item_policy.InputDependencies | None = None,
) -> tuple[bytes | None, RecoveryMetrics | None]:
    """Keep admitted current-turn semantics in one strictly smaller request."""
    if isinstance(budget, bool) or budget <= 0:
        return None, None
    portable = sanitize_responses_body(raw)
    if portable.body is None:
        return None, None
    original = _load_json(raw)
    payload = _load_json(portable.body)
    if not isinstance(original, dict) or not isinstance(payload, dict):
        return None, None
    original_items = original.get("input")
    input_items = payload.get("input")
    if not isinstance(original_items, list) or not isinstance(input_items, list):
        return None, None
    dependencies = portable.input_dependencies if input_dependencies is None else input_dependencies
    turn = item_policy.current_turn(input_items)
    if turn is None:
        return None, None
    retained = sorted(turn.retained | dependencies.required)
    if not dependencies.allows(set(retained)):
        return None, None
    dialogue = [cast(JsonObject, input_items[index]) for index in retained]
    candidate = dict(payload)
    candidate["input"] = dialogue
    try:
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None, None
    recovery = sanitize_responses_body(encoded).body
    if recovery is None or len(recovery) > budget or len(recovery) >= len(raw):
        return None, None
    dependencies.retain(retained)
    return recovery, RecoveryMetrics(
        original_bytes=len(raw),
        recovery_bytes=len(recovery),
        retained_messages=sum(item.get("type") == "message" for item in dialogue),
        dropped_input_items=len(original_items) - len(dialogue),
        prompt_cache_key_removed="prompt_cache_key" in original,
    )


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
        policy = item_policy.classify_input_item(typed_item)
        raw_type = typed_item.get("type")
        if policy is not None and "type" not in typed_item:
            raw_type = "message"
        self.item_types[_closed_label(raw_type, _KNOWN_ITEM_TYPES)] += 1
        self._observe_content(typed_item.get("content"))
        self.relationships.observe_item(typed_item)

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
