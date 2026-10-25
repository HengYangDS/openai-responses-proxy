"""Secret-free process telemetry and runtime status projection."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Protocol


class SanitizationMetrics(Protocol):
    """Replay metrics consumed without coupling telemetry to replay code."""

    @property
    def reasoning_items(self) -> int:
        """Return the number of removed reasoning items."""
        ...

    @property
    def encrypted_blocks(self) -> int:
        """Return the number of removed encrypted content blocks."""
        ...

    @property
    def local_image_items(self) -> int:
        """Return the number of removed local image items."""
        ...


_STARTED_AT = time.time()
_METRICS_LOCK = threading.Lock()
_COUNTERS = {
    "responses_received": 0,
    "responses_completed": 0,
    "responses_rejected_while_draining": 0,
    "drain_leases_expired": 0,
    "provider_rate_limits": 0,
    "provider_rate_limit_cooldown_hits": 0,
    "provider_unavailable_cooldown_hits": 0,
    "streams_completed": 0,
    "streams_incomplete": 0,
    "streams_failed": 0,
    "streams_pre_content_reconnect_attempts": 0,
    "streams_pre_content_exhausted": 0,
    "upstream_outcome_unknown": 0,
    "stream_projection_failures": 0,
    "response_failed_compaction_attempts": 0,
    "response_failed_compaction_accepted": 0,
    "response_failed_dialogue_recovery_attempts": 0,
    "response_failed_dialogue_recovery_accepted": 0,
    "response_failed_recovery_exhausted": 0,
    "input_variant_dialogue_recovery_attempts": 0,
    "input_variant_dialogue_recovery_accepted": 0,
    "input_variant_dialogue_recovery_exhausted": 0,
    "encrypted_replayed_reasoning_items_stripped": 0,
    "encrypted_replay_content_blocks_stripped": 0,
    "invalid_responses_success_bodies": 0,
    "unreplayable_images_stripped": 0,
    "wire_failure_retry_attempts": 0,
    "wire_failure_retry_accepted": 0,
    "wire_failure_recovery_exhausted": 0,
    "wire_failure_cooldown_hits": 0,
}
_UPSTREAM_CLASSIFICATIONS: dict[str, int] = {}
_LAST_FAILURE: dict[str, object] | None = None


def record_counter(name: str, amount: int = 1) -> None:
    """Record one secret-free runtime counter."""
    with _METRICS_LOCK:
        _COUNTERS[name] = _COUNTERS.get(name, 0) + max(0, amount)


def record_upstream_classification(name: str) -> None:
    """Record a bounded outcome class, never an upstream payload."""
    with _METRICS_LOCK:
        _UPSTREAM_CLASSIFICATIONS[name] = _UPSTREAM_CLASSIFICATIONS.get(name, 0) + 1


def record_failure(classification: str) -> None:
    """Retain only the latest failure class and time, never request data."""
    global _LAST_FAILURE
    with _METRICS_LOCK:
        _LAST_FAILURE = {"classification": classification, "at_unix": int(time.time())}


def record_sanitization(metrics: SanitizationMetrics) -> None:
    """Account for replay projection changes without parsing display text."""
    record_counter("encrypted_replayed_reasoning_items_stripped", metrics.reasoning_items)
    record_counter("encrypted_replay_content_blocks_stripped", metrics.encrypted_blocks)
    record_counter("unreplayable_images_stripped", metrics.local_image_items)


def status(
    *,
    release: str,
    serving_payload_sha256: str | None,
    release_receipt_sha256: str | None,
    admission: Mapping[str, object],
    runtime_identity: Mapping[str, object],
) -> dict[str, object]:
    """Return the secret-free telemetry, admission, and process identity."""
    with _METRICS_LOCK:
        counters = dict(sorted(_COUNTERS.items()))
        upstream = dict(sorted(_UPSTREAM_CLASSIFICATIONS.items()))
        last_failure = dict(_LAST_FAILURE) if _LAST_FAILURE is not None else None
    snapshot: dict[str, object] = {
        "release": release,
        "serving_payload_sha256": serving_payload_sha256,
        "release_receipt_sha256": release_receipt_sha256,
        "uptime_seconds": max(0, int(time.time() - _STARTED_AT)),
        "counters": counters,
        "upstream_classifications": upstream,
        "last_failure": last_failure,
    }
    snapshot.update(admission)
    snapshot.update(runtime_identity)
    return snapshot


def reset_for_test() -> None:
    """Reset telemetry state for deterministic unit tests."""
    global _LAST_FAILURE
    with _METRICS_LOCK:
        for name in _COUNTERS:
            _COUNTERS[name] = 0
        _UPSTREAM_CLASSIFICATIONS.clear()
        _LAST_FAILURE = None
