"""One upstream exchange and its bounded recovery state machine."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import field
from http.server import BaseHTTPRequestHandler
from typing import cast

import certifi

from openai_responses_proxy.protocol import response as live_response
from openai_responses_proxy.protocol.recovery import execution as execution_recovery
from openai_responses_proxy.protocol.recovery import input as input_recovery
from openai_responses_proxy.protocol.replay import items as item_policy
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay import upstream as upstream_transport
from openai_responses_proxy.relay.upstream import ExchangeDeadlineError
from openai_responses_proxy.relay.upstream import UpstreamResponse
from openai_responses_proxy.runtime import config as runtime_config

_SETTINGS = runtime_config.load()
UPSTREAM_TIMEOUT = _SETTINGS.upstream_timeout
RESPONSE_FAILED_COMPACTION_BUDGET = _SETTINGS.response_failed_compaction_budget
RESPONSE_FAILED_MAX_STAGES = _SETTINGS.response_failed_max_stages
RESPONSE_FAILED_DIALOGUE_SLOTS = 1
INPUT_VARIANT_DIALOGUE_SLOTS = 1
_MAX_ATTEMPTS = 4
_BACKOFFS = (0.4, 1.0, 2.0)


def _upstream_tls_context() -> ssl.SSLContext:
    """Build one verifying TLS context anchored to the packaged trust store.

    The native bundle ships no OpenSSL default trust directory, so host
    defaults cannot be relied on; certifi is the portable trust anchor.
    """
    return ssl.create_default_context(cafile=certifi.where())


_TLS_CONTEXT = _upstream_tls_context()
_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    upstream_transport.RefuseRedirectHandler(),
    urllib.request.HTTPSHandler(context=_TLS_CONTEXT),
)


def urlopen_direct(
    request: urllib.request.Request, timeout: float, *, deadline: float | None = None
) -> UpstreamResponse:
    """Open one upstream request without system or environment HTTP proxies."""
    opener = (
        _DIRECT_OPENER
        if deadline is None
        else upstream_transport.deadline_opener(deadline, _TLS_CONTEXT)
    )
    return cast(UpstreamResponse, opener.open(request, timeout=timeout))


def open_readonly(
    url: str, method: str, headers: dict[str, str], *, deadline: float
) -> UpstreamResponse:
    """Acquire success or HTTP-error headers once within the catalog deadline."""
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise ExchangeDeadlineError("catalog deadline expired before headers")
    try:
        response = urlopen_direct(_request(url, b"", method, headers), timeout, deadline=deadline)
    except urllib.error.HTTPError as error:
        response = cast(UpstreamResponse, error)
    if time.monotonic() >= deadline:
        response.close()
        raise ExchangeDeadlineError("catalog header deadline expired")
    return response


def _request(url: str, body: bytes, method: str, headers: dict[str, str]) -> urllib.request.Request:
    return urllib.request.Request(url, data=body or None, method=method, headers=headers)


@dataclass(slots=True)
class Exchange:
    """Carry one request through bounded upstream recovery attempts."""

    handler: BaseHTTPRequestHandler
    method: str
    request_id: int
    body: bytes
    url: str
    headers: dict[str, str]
    is_responses: bool
    attempt_body: bytes
    profile: provider_registry.Profile
    plaintext_agent_delivery: bool = False
    response_failed_stages: int = 0
    used_response_failed_compaction: bool = False
    compact_metrics: execution_recovery.RecoveryMetrics | None = None
    used_response_failed_dialogue: bool = False
    dialogue_metrics: execution_recovery.RecoveryMetrics | None = None
    used_input_variant_dialogue: bool = False
    input_variant_metrics: input_recovery.RecoveryMetrics | None = None
    response: UpstreamResponse | None = None
    input_dependencies: item_policy.InputDependencies = field(
        default_factory=item_policy.InputDependencies
    )
    deadline: float = field(default_factory=lambda: time.monotonic() + UPSTREAM_TIMEOUT)

    def upstream(
        self, body: bytes | None = None, *, deadline: float | None = None
    ) -> UpstreamResponse:
        """Open one attempt within the original exchange and any narrower deadline."""
        deadline = self.deadline if deadline is None else min(self.deadline, deadline)
        timeout = min(UPSTREAM_TIMEOUT, deadline - time.monotonic())
        if timeout <= 0:
            raise ExchangeDeadlineError("upstream deadline expired before header acquisition")
        try:
            response = urlopen_direct(
                _request(
                    self.url,
                    self.attempt_body if body is None else body,
                    self.method,
                    self.headers,
                ),
                timeout=timeout,
                deadline=deadline,
            )
        except Exception as error:
            if time.monotonic() >= deadline:
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                raise ExchangeDeadlineError("upstream header deadline expired") from error
            raise
        if time.monotonic() >= deadline:
            response.close()
            raise ExchangeDeadlineError("upstream header deadline expired")
        return response

    def pause(self, delay: float) -> None:
        """Wait for recovery only when a subsequent attempt still fits the budget."""
        if time.monotonic() + delay >= self.deadline:
            raise ExchangeDeadlineError("upstream deadline cannot admit the retry delay")
        time.sleep(delay)
        if time.monotonic() >= self.deadline:
            raise ExchangeDeadlineError("upstream deadline expired during retry delay")

    def log(self, event: str, detail: str = "") -> None:
        """Record one request-scoped operational event without sensitive payloads."""
        path = operational_log.safe_request_path(self.handler.path)
        operational_log.log(
            f"req={self.request_id} event={event} provider={self.profile.name} {detail}path={path}"
        )

    def accepted_recovery(self) -> None:
        """Record the response-failure recovery strategy accepted upstream."""
        if (
            not self.used_input_variant_dialogue
            and self.used_response_failed_dialogue
            and self.dialogue_metrics is not None
        ):
            telemetry.record_counter("response_failed_dialogue_recovery_accepted")
            m = self.dialogue_metrics
            self.log(
                "response_failed_dialogue_recovery_accepted",
                f"bytes={m['original_bytes']}->{m['recovery_bytes']} "
                f"retained_messages={m['retained_messages']} "
                f"dropped_input_items={m['dropped_input_items']} "
                f"pair_safe_stages={self.response_failed_stages} ",
            )
        elif self.used_response_failed_compaction and self.compact_metrics is not None:
            telemetry.record_counter("response_failed_compaction_accepted")
            m = self.compact_metrics
            self.log(
                "response_failed_compact_recovery_accepted",
                f"bytes={m['original_bytes']}->{m['compact_bytes']} "
                f"removed_inputs={m['removed_inputs']} retained_inputs={m['retained_inputs']} ",
            )

    def input_variant_accepted(self) -> None:
        """Record an input-variant dialogue recovery accepted upstream."""
        if not (self.used_input_variant_dialogue and self.input_variant_metrics):
            return
        telemetry.record_counter("input_variant_dialogue_recovery_accepted")
        m = self.input_variant_metrics
        self.log(
            "input_variant_dialogue_recovery_accepted",
            f"bytes={m.original_bytes}->{m.recovery_bytes} "
            f"retained_messages={m.retained_messages} "
            f"dropped_input_items={m.dropped_input_items} ",
        )

    def input_variant_exhausted(self, detail: str) -> None:
        """Record exhausted input-variant recovery with bounded diagnostic detail."""
        telemetry.record_counter("input_variant_dialogue_recovery_exhausted")
        telemetry.record_failure("input_variant_dialogue_recovery_exhausted")
        self.log("input_variant_dialogue_recovery_exhausted", detail)


def _classification(
    status_code: int,
    payload: bytes,
    disposition: str,
    exact: bool,
    wire_failure: bool,
) -> str:
    if status_code == 400:
        try:
            document = json.loads(payload)
        except (TypeError, ValueError, RecursionError):
            document = None
        error = document.get("error") if isinstance(document, dict) else None
        if (
            isinstance(error, dict)
            and error.get("type") == "invalid_request_error"
            and error.get("code") == "invalid_encrypted_content"
        ):
            return "provider_bound_encrypted_content"
    lower = payload.lower()
    blocked = all(map(lower.__contains__, (b'"code":"invalid_prompt"', b"request blocked")))
    special = {
        (True, 400, "", False): "input_variant_validation_error",
        (False, 400, "full", False): "response_failed",
        (False, 400, "full", True): "blocked_invalid_prompt",
    }.get((exact, status_code, disposition, blocked))
    if wire_failure:
        return "wire_policy_failure"
    return special or "_".join(filter(None, (f"http_{status_code}", disposition)))


def _recover_input_variant(exchange: Exchange) -> bool:
    diagnostic = input_recovery.diagnose(exchange.attempt_body)
    exchange.log(
        "input_variant_validation_error",
        f"{input_recovery.format_diagnostic(diagnostic)} ",
    )
    recovery, metrics = input_recovery.build_recovery(
        exchange.attempt_body,
        RESPONSE_FAILED_COMPACTION_BUDGET,
        input_dependencies=exchange.input_dependencies,
    )
    if recovery is None or metrics is None:
        return False
    telemetry.record_counter("input_variant_dialogue_recovery_attempts")
    exchange.used_input_variant_dialogue = True
    exchange.input_variant_metrics = metrics
    previous_bytes = len(exchange.attempt_body)
    exchange.attempt_body = recovery
    exchange.log(
        "input_variant_dialogue_recovery",
        f"bytes={previous_bytes}->{metrics.recovery_bytes} "
        f"retained_messages={metrics.retained_messages} "
        f"dropped_input_items={metrics.dropped_input_items} "
        f"cache_key_removed={metrics.prompt_cache_key_removed} ",
    )
    return True


def _recover_response_failed(exchange: Exchange, status_code: int, disposition: str) -> bool:
    if not (exchange.is_responses and status_code == 400 and disposition == "full"):
        return False
    if exchange.response_failed_stages < RESPONSE_FAILED_MAX_STAGES:
        compact, metrics = execution_recovery.compact_request(
            exchange.attempt_body,
            RESPONSE_FAILED_COMPACTION_BUDGET,
            input_dependencies=exchange.input_dependencies,
        )
        if compact is not None and metrics is not None:
            telemetry.record_counter("response_failed_compaction_attempts")
            exchange.response_failed_stages += 1
            metrics["stage"] = exchange.response_failed_stages
            exchange.compact_metrics = metrics
            exchange.used_response_failed_compaction = True
            previous_bytes = len(exchange.attempt_body)
            exchange.attempt_body = compact
            exchange.log(
                "response_failed_compact_recovery",
                f"stage={exchange.response_failed_stages}/{RESPONSE_FAILED_MAX_STAGES} "
                f"bytes={previous_bytes}->{metrics['compact_bytes']} budget={metrics['budget_bytes']} "
                f"removed_inputs={metrics['removed_inputs']} retained_inputs={metrics['retained_inputs']} "
                f"cache_key_removed={metrics['prompt_cache_key_removed']} "
                f"budget_met={metrics.get('budget_met', True)} ",
            )
            return True
    if exchange.used_response_failed_dialogue:
        return False
    recovery, metrics = execution_recovery.recover_dialogue(
        exchange.attempt_body,
        RESPONSE_FAILED_COMPACTION_BUDGET,
        input_dependencies=exchange.input_dependencies,
    )
    if recovery is None or metrics is None:
        return False
    telemetry.record_counter("response_failed_dialogue_recovery_attempts")
    exchange.used_response_failed_dialogue = True
    exchange.dialogue_metrics = metrics
    previous_bytes = len(exchange.attempt_body)
    exchange.attempt_body = recovery
    exchange.log(
        "response_failed_dialogue_recovery",
        f"bytes={previous_bytes}->{metrics['recovery_bytes']} "
        f"retained_messages={metrics['retained_messages']} "
        f"dropped_input_items={metrics['dropped_input_items']} "
        f"cache_key_removed={metrics['prompt_cache_key_removed']} ",
    )
    return True


def _exhaust_response_failed(exchange: Exchange) -> None:
    telemetry.record_counter("response_failed_recovery_exhausted")
    telemetry.record_failure("response_failed_recovery_exhausted")
    attempts = exchange.response_failed_stages + int(exchange.used_response_failed_dialogue) + 1
    downstream.send_payload(
        exchange.handler,
        503,
        execution_recovery.exhausted_payload(attempts),
        retry_after="3",
    )
    exchange.log(
        "response_failed_recovery_exhausted",
        f"attempts={attempts} pair_safe_stages={exchange.response_failed_stages} "
        f"dialogue_recovery={exchange.used_response_failed_dialogue} upstream_status=400 ",
    )


def _reject_wire_failure(
    exchange: Exchange, fingerprint: str, attempts: int, event: str, detail: str
) -> None:
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


def _retry_wire_failure(exchange: Exchange) -> bool:
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
        _reject_wire_failure(
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
        _reject_wire_failure(
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


def _http_error(exchange: Exchange, error: urllib.error.HTTPError, attempt: int) -> str:
    try:
        payload = upstream_transport.read_body(
            cast(UpstreamResponse, error), exchange.deadline, downstream.MAX_RESPONSES_JSON_BYTES
        )
        status_code, headers = error.code, error.headers
    except ExchangeDeadlineError:
        raise
    except Exception as failure:
        reason = (
            "response_too_large"
            if isinstance(failure, upstream_transport.ResponseSizeError)
            else operational_log.safe_exception_label(failure)
        )
        telemetry.record_failure("upstream_error_body_unreadable")
        downstream.send_payload(
            exchange.handler,
            502,
            live_response.error_payload(
                "Upstream error body could not be read safely; retry the turn",
                "upstream_unavailable",
                "upstream_error_body_unreadable",
                reason=reason,
            ),
        )
        exchange.log(
            "upstream_error_body_unreadable", f"upstream_status={error.code} reason={reason} "
        )
        return "terminal"
    finally:
        error.close()
    portable = execution_recovery.retry_disposition(status_code, payload)
    policy = exchange.profile.wire_policy
    rate_limited = exchange.is_responses and status_code == 429
    wire_failure = (
        not rate_limited
        and policy is not None
        and policy.is_retryable_failure(status_code, payload)
    )
    disposition = "full" if wire_failure else portable
    exact = input_recovery.is_exact_validation_error(status_code, payload)
    classification = _classification(status_code, payload, disposition, exact, wire_failure)
    telemetry.record_upstream_classification(classification)
    if rate_limited:
        seconds = cooldown.retry_after_seconds(headers)
        cooldown.remember_failure(
            cooldown.provider_key(exchange.profile.name), cooldown_seconds=seconds
        )
        telemetry.record_counter("provider_rate_limits")
        exchange.log("provider_rate_limit", f"cooldown_seconds={seconds} ")
    if exchange.used_input_variant_dialogue:
        exchange.input_variant_exhausted(f"upstream_status={status_code} ")
        downstream.relay_error(exchange.handler, status_code, headers, payload)
        return "terminal"
    if exact and _recover_input_variant(exchange):
        return "retry"
    if _recover_response_failed(exchange, status_code, disposition):
        return "retry"
    if exchange.is_responses and status_code == 400 and portable == "full":
        _exhaust_response_failed(exchange)
        return "terminal"
    retry_ceiling = 1 if disposition == "once" else _MAX_ATTEMPTS - 1
    transient_retries_used = attempt - exchange.response_failed_stages
    if disposition and transient_retries_used < retry_ceiling and status_code not in (400, 477):
        delay = 3.0 if disposition == "once" else _BACKOFFS[min(attempt, len(_BACKOFFS) - 1)]
        exchange.log(
            "upstream_retry",
            f"status={status_code} disposition={disposition} attempt={attempt + 1}/{retry_ceiling} "
            f"delay_seconds={delay} ",
        )
        exchange.pause(delay)
        return "retry"
    if wire_failure:
        return "terminal" if _retry_wire_failure(exchange) else "accepted"
    if status_code == 503:
        seconds = cooldown.retry_after_seconds(headers)
        cooldown.remember_failure(
            cooldown.unavailable_key(exchange.profile.name), cooldown_seconds=seconds
        )
        exchange.log("provider_unavailable_cooldown_started", f"seconds={seconds} ")
    downstream.relay_error(exchange.handler, status_code, headers, payload)
    telemetry.record_failure(classification)
    exchange.log(
        "upstream_http_terminal",
        f"status={status_code} response_bytes={len(payload)} attempts={attempt + 1} ",
    )
    return "terminal"


def _transport_error(exchange: Exchange, error: Exception, attempt: int) -> str:
    if exchange.used_input_variant_dialogue:
        exchange.input_variant_exhausted(f"{operational_log.safe_exception_context(error)} ")
        downstream.send_payload(
            exchange.handler,
            502,
            live_response.error_payload(
                "Upstream input-variant recovery transport failed; retry the turn",
                "upstream_unavailable",
                "input_variant_recovery_transport_error",
            ),
        )
        return "terminal"
    if attempt < _MAX_ATTEMPTS - 1:
        exchange.log(
            "upstream_transport_retry",
            f"attempt={attempt + 1} {operational_log.safe_exception_context(error)} ",
        )
        exchange.pause(_BACKOFFS[min(attempt, len(_BACKOFFS) - 1)])
        return "retry"
    telemetry.record_failure("upstream_transport_error")
    downstream.send_payload(
        exchange.handler,
        502,
        live_response.error_payload(
            "Upstream transport failed after bounded retries; retry the turn",
            "upstream_unavailable",
            "upstream_transport_error",
        ),
    )
    exchange.log(
        "upstream_transport_exhausted",
        f"{operational_log.safe_exception_context(error)} ",
    )
    return "terminal"


def open_upstream(exchange: Exchange) -> UpstreamResponse | None:
    """Open an upstream response or emit the bounded terminal error."""
    stages = RESPONSE_FAILED_MAX_STAGES if exchange.is_responses else 0
    dialogue_slots = RESPONSE_FAILED_DIALOGUE_SLOTS if exchange.is_responses else 0
    input_slots = INPUT_VARIANT_DIALOGUE_SLOTS if exchange.is_responses else 0
    try:
        for attempt in range(
            (_MAX_ATTEMPTS if exchange.is_responses else 1) + stages + dialogue_slots + input_slots
        ):
            try:
                response = exchange.upstream()
            except urllib.error.HTTPError as error:
                outcome = _http_error(exchange, error, attempt)
                if outcome == "retry":
                    continue
                return exchange.response if outcome == "accepted" else None
            except ExchangeDeadlineError:
                raise
            except Exception as error:
                if _transport_error(exchange, error, attempt) == "retry":
                    continue
                return None
            exchange.accepted_recovery()
            return response
    except ExchangeDeadlineError:
        downstream.deadline_exceeded(exchange)
        return None
    telemetry.record_failure("upstream_transport_error")
    downstream.send_payload(
        exchange.handler,
        502,
        live_response.error_payload(
            "Upstream transport failed after bounded retries; retry the turn",
            "upstream_unavailable",
            "upstream_transport_error",
        ),
    )
    return None
