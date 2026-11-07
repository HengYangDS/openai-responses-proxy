"""Provider-neutral bounded cooldown for repeated upstream failures."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from datetime import UTC
from datetime import datetime
from email.message import Message
from email.utils import parsedate_to_datetime

_FAILURES_LOCK = threading.Lock()
_FAILURES: dict[str, float] = {}
RATE_LIMIT_FALLBACK_SECONDS = 5
RATE_LIMIT_MAX_SECONDS = 300


def _purge_expired_locked(now: float) -> None:
    expired = tuple(key for key, expires_at in _FAILURES.items() if expires_at <= now)
    for key in expired:
        del _FAILURES[key]


def remember_failure(
    key: str,
    *,
    capacity: int = 1024,
    cooldown_seconds: int = 30,
    now: float | None = None,
) -> None:
    """Record one failure in a bounded monotonic cooldown cache."""
    moment = time.monotonic() if now is None else now
    with _FAILURES_LOCK:
        _purge_expired_locked(moment)
        _FAILURES[key] = max(_FAILURES.get(key, moment), moment + cooldown_seconds)
        oldest = sorted(_FAILURES, key=_FAILURES.__getitem__)
        for oldest_key in oldest[: -max(1, capacity)]:
            del _FAILURES[oldest_key]


def remaining(
    key: str,
    *,
    now: float | None = None,
) -> float:
    """Return remaining cooldown seconds, purging expired entries first."""
    moment = time.monotonic() if now is None else now
    with _FAILURES_LOCK:
        _purge_expired_locked(moment)
        expires_at = _FAILURES.get(key)
    return 0 if expires_at is None else max(0, expires_at - moment)


def retry_after_seconds(
    headers: Mapping[str, str] | Message[str, str], *, now: datetime | None = None
) -> int:
    """Interpret bounded HTTP retry timing or return the release-owned fallback."""
    raw = headers.get("Retry-After", "").strip()
    if raw.isascii() and raw.isdecimal():
        significant = raw.lstrip("0")
        # A value with more digits than the cap exceeds it, so it is never parsed.
        seconds = (
            RATE_LIMIT_MAX_SECONDS
            if len(significant) > len(str(RATE_LIMIT_MAX_SECONDS))
            else int(significant or "0")
        )
    else:
        try:
            target = parsedate_to_datetime(raw)
            current = datetime.now(UTC) if now is None else now
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            seconds = int((target - current).total_seconds() + 0.999)
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    if seconds <= 0:
        return RATE_LIMIT_FALLBACK_SECONDS
    return min(seconds, RATE_LIMIT_MAX_SECONDS)


def provider_key(provider: str) -> str:
    """Return the collision-free key for one provider-scoped cooldown."""
    return f"provider:{provider}"


def unavailable_key(provider: str) -> str:
    """Keep upstream unavailability separate from rate-limit cooldowns."""
    return f"unavailable:{provider}"


def failure_count_for_test() -> int:
    """Expose only cache cardinality for bounded-state tests."""
    with _FAILURES_LOCK:
        return len(_FAILURES)


def has_failure_for_test(key: str) -> bool:
    """Report cooldown-key membership without exposing mutable state."""
    with _FAILURES_LOCK:
        return key in _FAILURES


def reset_for_test() -> None:
    """Reset cooldown state for deterministic unit tests."""
    with _FAILURES_LOCK:
        _FAILURES.clear()
