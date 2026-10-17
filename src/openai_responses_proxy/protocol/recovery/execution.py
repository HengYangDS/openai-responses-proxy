"""Classify and recover bounded upstream Responses execution failures.

This owner admits recovery only for explicit provider execution failures. It
never edits stored history: each fallback is a strictly smaller request-local
projection that preserves the latest user request and, when possible, complete
tool call/output relationships.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from openai_responses_proxy.protocol import response
from openai_responses_proxy.protocol.recovery import input as input_recovery
from openai_responses_proxy.protocol.replay import items as item_policy
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body

type JsonObject = dict[str, object]
type Request = tuple[JsonObject, list[object], int]
type RecoveryMetrics = dict[str, int | bool]
type Recovery = tuple[bytes | None, RecoveryMetrics | None]

COMPACTION_RATIO_DENOMINATOR = 2


def _request(raw: bytes, minimum_items: int = 1) -> Request | None:
    """Decode a request with enough input items and return its latest user index."""
    try:
        decoded: object = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        return None
    payload: JsonObject = {key: value for key, value in decoded.items() if isinstance(key, str)}
    items = payload.get("input")
    if not isinstance(items, list) or len(items) < minimum_items:
        return None
    latest_user = next(
        (
            index
            for index in range(len(items) - 1, -1, -1)
            if isinstance(items[index], dict)
            and items[index].get("type") == "message"
            and items[index].get("role") == "user"
        ),
        -1,
    )
    typed_items: list[object] = list(items)
    return (payload, typed_items, latest_user) if latest_user >= 0 else None


def _budget(raw: bytes, budget: int) -> int | None:
    """Bound a positive caller budget by the required compaction ratio."""
    if budget <= 0:
        return None
    return min(budget, max(1, len(raw) // COMPACTION_RATIO_DENOMINATOR))


def retry_disposition(code: int, err_body: bytes) -> str:
    """Classify an upstream failure for bounded request-local recovery."""
    if code in (500, 502, 503, 504, 524):
        return "full"
    if code != 400:
        return ""
    try:
        document = json.loads(err_body)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    error = document.get("error") if isinstance(document, dict) else None
    if not isinstance(error, dict):
        return ""
    error_type = error.get("type")
    error_code = error.get("code")
    if error_code == "response_failed" and isinstance(error_type, str) and error_type:
        return "full"
    message = error.get("message")
    if (
        error_code == "invalid_prompt"
        and error_type == "invalid_request_error"
        and isinstance(message, str)
        and message.startswith("Request blocked.")
    ):
        return "full"
    if error_code == "invalid_payload":
        return "once"
    return ""


def exhausted_payload(attempts: int) -> bytes:
    """Return the retryable terminal payload after all bounded recovery fails."""
    return response.error_payload(
        "Upstream rejected bounded Responses recovery; retry the turn",
        "upstream_unavailable",
        "response_failed_recovery_exhausted",
        attempts=attempts,
    )


def tool_pair_boundary_is_safe(items: Sequence[object], start: int) -> bool:
    """Return whether a retained suffix has valid ordered tool relationships."""
    relationships = item_policy.ToolRelationships()
    for item in items[start:]:
        if not isinstance(item, dict):
            continue
        item = cast(JsonObject, item)
        if relationships.observe(
            item.get("type"), item.get("call_id"), name=item.get("name"), item_id=item.get("id")
        ):
            return False
    return True


def compact_request(
    raw: bytes,
    budget: int,
    *,
    input_dependencies: item_policy.InputDependencies | None = None,
) -> Recovery:
    """Build the oldest-prefix-only, pair-safe fallback for one failed request."""
    bounded_budget = _budget(raw, budget)
    portable = sanitize_responses_body(raw)
    request = _request(portable.body, minimum_items=2) if portable.body is not None else None
    if bounded_budget is None or request is None:
        return None, None
    payload, input_items, latest_user_index = request
    dependencies = portable.input_dependencies if input_dependencies is None else input_dependencies

    smallest = None
    for start in range(1, latest_user_index + 1):
        if not tool_pair_boundary_is_safe(input_items, start):
            continue
        indices = [
            index
            for index, item in enumerate(input_items)
            if index >= start
            or item_policy.is_required_current_turn_item(item)
            or index in dependencies.required
        ]
        if not dependencies.allows(set(indices)):
            continue
        retained = [input_items[index] for index in indices]
        candidate = dict(payload)
        candidate["input"] = retained
        candidate["store"] = False
        candidate.pop("prompt_cache_key", None)
        compact = json.dumps(candidate, separators=(",", ":")).encode("utf-8")
        compact = sanitize_responses_body(compact).body
        if compact is None:
            continue
        metrics = {
            "original_bytes": len(raw),
            "budget_bytes": bounded_budget,
            "compact_bytes": len(compact),
            "removed_inputs": len(input_items) - len(retained),
            "retained_inputs": len(retained),
            "prompt_cache_key_removed": "prompt_cache_key" in payload,
        }
        if len(compact) <= bounded_budget:
            dependencies.retain(indices)
            return compact, metrics
        if len(compact) < len(raw) and (smallest is None or len(compact) < len(smallest[0])):
            smallest = (compact, metrics, indices)
    if smallest is not None:
        compact, metrics, indices = smallest
        metrics["budget_met"] = False
        dependencies.retain(indices)
        return compact, metrics
    return None, None


def recover_dialogue(
    raw: bytes,
    budget: int,
    *,
    input_dependencies: item_policy.InputDependencies | None = None,
) -> Recovery:
    """Apply the shared current-turn recovery within the execution-failure budget."""
    bounded_budget = _budget(raw, budget)
    if bounded_budget is None:
        return None, None
    recovery, metrics = input_recovery.build_recovery(
        raw, bounded_budget, input_dependencies=input_dependencies
    )
    if recovery is None or metrics is None:
        return None, None
    return recovery, {
        "original_bytes": metrics.original_bytes,
        "recovery_bytes": metrics.recovery_bytes,
        "budget_bytes": bounded_budget,
        "retained_messages": metrics.retained_messages,
        "dropped_input_items": metrics.dropped_input_items,
        "prompt_cache_key_removed": metrics.prompt_cache_key_removed,
    }
