"""Recovery from a provider wire-policy failure: one retry, then a cooldown and an exhausted reply."""

from __future__ import annotations

import urllib.error
from typing import TYPE_CHECKING

from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay.upstream import ExchangeDeadlineError

if TYPE_CHECKING:
    from openai_responses_proxy.relay.exchange import Exchange


def reject(exchange: Exchange, fingerprint: str, attempts: int, event: str, detail: str) -> None:
    """Remember the failing request, start its cooldown, and send the exhausted reply."""
    policy = exchange.profile.wire_policy
    if policy is None:
        raise RuntimeError("wire recovery requires a provider policy")
    telemetry.record_counter("wire_failure_recovery_exhausted")
    telemetry.record_failure("wire_failure_recovery_exhausted")
    cooldown.remember_failure(
        fingerprint,
        capacity=policy.FAILURE_CACHE_CAPACITY,
        cooldown_seconds=policy.FAILURE_COOLDOWN_SECONDS,
    )
    downstream.send_wire_failure_exhausted(exchange.handler, policy, attempts)
    exchange.log(event, detail)


def retry(exchange: Exchange) -> bool:
    """Retry a wire-policy failure once; return whether the exchange ended with the exhausted reply."""
    policy = exchange.profile.wire_policy
    if policy is None:
        return False
    fingerprint = policy.request_fingerprint(exchange.body)
    telemetry.record_counter("wire_failure_retry_attempts")
    exchange.log(
        "wire_failure_retry",
        f"bytes={len(exchange.attempt_body)} policy={policy.POLICY_VERSION} ",
    )
    try:
        exchange_response = exchange.upstream()
    except urllib.error.HTTPError as error:
        try:
            status = error.code
        finally:
            error.close()
        reject(
            exchange,
            fingerprint,
            2,
            "wire_failure_retry_failed",
            f"upstream_status={status} attempts=2 ",
        )
        return True
    except ExchangeDeadlineError:
        raise
    except Exception as error:
        reject(
            exchange,
            fingerprint,
            2,
            "wire_failure_retry_failed",
            f"exception={operational_log.safe_exception_label(error)} attempts=2 ",
        )
        return True
    telemetry.record_counter("wire_failure_retry_accepted")
    exchange.log("wire_failure_retry_accepted")
    exchange.response = exchange_response
    return False
