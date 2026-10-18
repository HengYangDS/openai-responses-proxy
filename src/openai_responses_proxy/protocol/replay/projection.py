"""Normalize a Responses request envelope and report its replay outcome.

Input grammar and relationship projection belong to the replay input owner.
Request-local normalization never changes the client's conversation history.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import cast

from openai_responses_proxy.protocol.replay import input as replay_input
from openai_responses_proxy.protocol.replay import items as item_policy
from openai_responses_proxy.protocol.replay.content import ProjectionRejectedError
from openai_responses_proxy.protocol.replay.content import reject as _reject

type JsonObject = dict[str, object]

_PROVIDER_BINDINGS = ("previous_response_id", "conversation", "prompt_cache_key")


@dataclass(frozen=True, slots=True)
class ProjectionMetrics:
    """Secret-free aggregate changes made by one replay projection."""

    provider_bindings: int = 0
    reasoning_items: int = 0
    reference_items: int = 0
    search_items: int = 0
    item_ids: int = 0
    encrypted_blocks: int = 0
    omission_markers: int = 0
    local_image_items: int = 0
    empty_tool_outputs: int = 0
    changed_items: int = 0
    store_normalized: bool = False
    include_trimmed: bool = False


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Replay outcome with wire data, request provenance and diagnostics separate."""

    body: bytes | None
    status: str
    metrics: ProjectionMetrics = ProjectionMetrics()
    reason: str | None = None
    input_dependencies: item_policy.InputDependencies = dataclass_field(
        default_factory=item_policy.InputDependencies, repr=False, compare=False
    )

    def diagnostic(self) -> str:
        """Project the structured outcome into one bounded operational note."""
        if self.status == "rejected":
            return f"rejected {self.reason or 'unknown'}"
        if self.status == "clean":
            return "clean portable_replay"
        metrics = self.metrics
        return (
            f"projected provider_bindings={metrics.provider_bindings} "
            f"reasoning_items={metrics.reasoning_items} "
            f"reference_items={metrics.reference_items} "
            f"search_items={metrics.search_items} "
            f"item_ids={metrics.item_ids} "
            f"encrypted_blocks={metrics.encrypted_blocks} "
            f"omission_markers={metrics.omission_markers} "
            f"local_image_items={metrics.local_image_items} "
            f"empty_tool_outputs={metrics.empty_tool_outputs} "
            f"store_normalized={metrics.store_normalized} "
            f"include_trimmed={metrics.include_trimmed}"
        )


def _finite_json_number(value: str) -> float:
    """Require a finite native representation for every decoded JSON number."""
    number = float(value)
    if not math.isfinite(number):
        _reject("invalid_json")
    return number


def sanitize_responses_body(raw: bytes) -> ProjectionResult:
    """Return portable request data and typed secret-free projection metrics."""
    try:
        payload = json.loads(
            raw, parse_float=_finite_json_number, parse_constant=_finite_json_number
        )
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ProjectionResult(None, "rejected", reason="invalid_json")
    if not isinstance(payload, dict):
        return ProjectionResult(None, "rejected", reason="request_not_object")

    candidate = cast(JsonObject, dict(payload))
    store_normalized = candidate.get("store") is not False
    candidate["store"] = False
    provider_bindings = sum(field in candidate for field in _PROVIDER_BINDINGS)
    for field in _PROVIDER_BINDINGS:
        candidate.pop(field, None)

    include_trimmed = False
    include = candidate.get("include")
    if include is not None:
        if not isinstance(include, list) or any(not isinstance(value, str) for value in include):
            return ProjectionResult(None, "rejected", reason="invalid_include")
        trimmed = [value for value in include if value != "reasoning.encrypted_content"]
        include_trimmed = len(trimmed) != len(include)
        candidate["include"] = trimmed

    raw_input = candidate.get("input")
    input_dependencies = item_policy.InputDependencies()
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
    try:
        if isinstance(raw_input, str):
            if not raw_input:
                _reject("empty_input")
        elif isinstance(raw_input, list):
            candidate["input"], metrics, input_dependencies = replay_input.project_input(
                cast("list[object]", raw_input)
            )
        else:
            _reject("invalid_input")
    except (ProjectionRejectedError, RecursionError) as exc:
        reason = "projection_depth_exceeded" if isinstance(exc, RecursionError) else str(exc)
        return ProjectionResult(None, "rejected", reason=reason)

    changed = bool(
        store_normalized
        or provider_bindings
        or include_trimmed
        or any(metrics.values())
        or candidate != payload
    )
    if not changed:
        return ProjectionResult(raw, "clean", input_dependencies=input_dependencies)
    try:
        encoded = json.dumps(
            candidate, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return ProjectionResult(None, "rejected", reason="serialization_failed")
    return ProjectionResult(
        encoded,
        "projected",
        ProjectionMetrics(
            provider_bindings=provider_bindings,
            reasoning_items=metrics["reasoning_items"],
            reference_items=metrics["reference_items"],
            search_items=metrics["search_items"],
            item_ids=metrics["item_ids"],
            encrypted_blocks=metrics["encrypted_blocks"],
            omission_markers=metrics["omission_markers"],
            local_image_items=metrics["local_image_items"],
            empty_tool_outputs=metrics["empty_tool_outputs"],
            changed_items=metrics["changed_items"],
            store_normalized=store_normalized,
            include_trimmed=include_trimmed,
        ),
        input_dependencies=input_dependencies,
    )
