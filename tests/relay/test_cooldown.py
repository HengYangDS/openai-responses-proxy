"""Unit contracts for provider-neutral bounded failure cooldowns."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy.relay import cooldown

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[2]


class CooldownTests:
    def setup_method(self) -> None:
        cooldown.reset_for_test()

    def test_cache_evicts_and_expires_without_retaining_payloads(self) -> None:
        cooldown.remember_failure("expired", now=10.0)
        assert cooldown.has_failure_for_test("expired")
        assert cooldown.remaining("expired", now=40.0) == 0
        for index in range(4):
            cooldown.remember_failure(f"key-{index}", capacity=3, now=100.0 + index)
        assert cooldown.failure_count_for_test() == 3
        assert not cooldown.has_failure_for_test("key-0")

    def test_expired_entries_are_purged_by_reads_and_unrelated_writes(self) -> None:
        cooldown.remember_failure("read", cooldown_seconds=30, now=100.0)
        assert cooldown.remaining("read", now=130.1) == 0
        assert not cooldown.has_failure_for_test("read")

        cooldown.remember_failure("stale", cooldown_seconds=30, now=300.0)
        cooldown.remember_failure("other", cooldown_seconds=30, now=330.1)
        assert not cooldown.has_failure_for_test("stale")
        assert cooldown.has_failure_for_test("other")

    def test_concurrent_writes_stay_within_capacity(self) -> None:
        capacity = 64

        def remember(index: int) -> None:
            cooldown.remember_failure(f"key-{index}", capacity=capacity, now=200.0 + index / 10_000)

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(remember, range(capacity + 100)))
        assert cooldown.failure_count_for_test() <= capacity
        assert not cooldown.has_failure_for_test("key-0")

    def test_retry_after_supports_delta_date_and_bounded_fallback(
        self, subtests: pytest.Subtests
    ) -> None:
        delta = Message()
        delta["Retry-After"] = "7"
        assert cooldown.retry_after_seconds(delta, now=datetime.now(UTC)) == 7

        dated = Message()
        dated["Retry-After"] = "Sun, 02 Aug 2026 04:26:45 GMT"
        now = datetime(2026, 8, 2, 4, 26, 35, tzinfo=UTC)
        assert cooldown.retry_after_seconds(dated, now=now) == 10

        for value in (None, "invalid", "-1", "0"):
            headers = Message()
            if value is not None:
                headers["Retry-After"] = value
            with subtests.test(value=value):
                assert cooldown.retry_after_seconds(headers, now=now) == 5

        for value in ("301", "99999", "9" * 10_000):
            headers = Message()
            headers["Retry-After"] = value
            with subtests.test(value=value[:20]):
                assert cooldown.retry_after_seconds(headers, now=now) == 300

        naive = Message()
        naive["Retry-After"] = "Sun, 02 Aug 2026 04:26:45"
        assert cooldown.retry_after_seconds(naive, now=now) == 10

    def test_later_shorter_failure_does_not_shorten_an_active_cooldown(self) -> None:
        cooldown.remember_failure("provider:ucloud", cooldown_seconds=300, now=10.0)
        cooldown.remember_failure("provider:ucloud", cooldown_seconds=5, now=11.0)
        assert cooldown.remaining("provider:ucloud", now=12.0) == 298.0
