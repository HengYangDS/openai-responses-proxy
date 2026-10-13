"""Provider-scoped rate-limit transport contracts."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import http_error
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = provider_registry.load()


class TestRateLimitTransport(InputTransportFixture):
    def test_rate_limit_is_relayed_after_one_upstream_attempt_without_sleep(
        self, *, mocker
    ) -> None:
        payload = b'{"error":{"message":"provider concurrency limit reached"}}'
        headers = Message()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Retry-After"] = "7"
        headers["X-Request-Id"] = "rate-limit-request"

        def rate_limited(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                "https://upstream.test/v1/responses",
                429,
                "Too Many Requests",
                headers,
                io.BytesIO(payload),
            )

        handler = MemoryHandler(json.dumps({"input": []}).encode(), path="/ucloud/v1/responses")
        open_ = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=rate_limited)
        sleep = mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        responses.relay(handler, "POST", PROVIDERS)

        assert open_.call_count == 1
        sleep.assert_not_called()
        assert handler.statuses == [429]
        assert handler.output() == payload
        assert ("Retry-After", "7") in handler.sent_headers
        assert ("X-Request-Id", "rate-limit-request") in handler.sent_headers

    def test_rate_limit_contract_holds_across_real_loopback_http(self) -> None:
        payload = b'{"error":{"message":"provider concurrency limit reached"}}'
        with running_proxy([(429, payload)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(
                    port,
                    json.dumps({"input": []}).encode(),
                    path="/ucloud/v1/responses",
                )
            with raised.value as error:
                assert error.code == 429
                assert error.read() == payload

        assert len(received) == 1

    def test_rate_limit_bypasses_even_a_broad_provider_wire_retry_policy(self, *, mocker) -> None:
        payload = b'{"error":{"message":"provider concurrency limit reached"}}'
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Retry-After"] = "7"
        policy = mocker.Mock()
        policy.request_fingerprint.return_value = "test-wire-fingerprint"
        policy.is_retryable_failure.return_value = True
        policy.POLICY_VERSION = "test-wire-policy"
        profiles = dict(PROVIDERS.profiles)
        current = profiles["ucloud"]
        profiles["ucloud"] = type(current)(current.name, current.base_url, policy)
        registry = type(PROVIDERS)(profiles)

        def rate_limited(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                "https://upstream.test/v1/responses",
                429,
                "Too Many Requests",
                headers,
                io.BytesIO(payload),
            )

        handler = MemoryHandler(json.dumps({"input": []}).encode(), path="/ucloud/v1/responses")
        open_ = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=rate_limited)
        sleep = mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        responses.relay(handler, "POST", registry)

        assert open_.call_count == 1
        sleep.assert_not_called()
        assert handler.statuses == [429]
        assert handler.output() == payload

    def test_rate_limit_cooldown_is_provider_scoped_and_skips_upstream(self, *, mocker) -> None:
        payload = b'{"error":{"message":"provider concurrency limit reached"}}'
        first = MemoryHandler(json.dumps({"input": []}).encode(), path="/ucloud/v1/responses")
        second = MemoryHandler(json.dumps({"input": []}).encode(), path="/ucloud/v1/responses")
        other = MemoryHandler(json.dumps({"input": []}).encode(), path="/aihubmix/v1/responses")
        open_ = mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            side_effect=[
                http_error(429, "Too Many Requests", payload),
                DirectResponse(b'{"id":"resp_other","status":"completed"}'),
            ],
        )
        responses.relay(first, "POST", PROVIDERS)
        responses.relay(second, "POST", PROVIDERS)
        responses.relay(other, "POST", PROVIDERS)

        assert open_.call_count == 2
        assert first.statuses == [429]
        assert second.statuses == [429]
        assert b"provider_rate_limit_cooldown" in second.output()
        assert ("Retry-After", "5") in second.sent_headers
        assert other.statuses == [200]
        assert other.output() == b'{"id":"resp_other","status":"completed"}'

    def test_rate_limit_cooldown_rejects_before_lifecycle_admission(self, *, mocker) -> None:
        cooldown.remember_failure(
            cooldown.provider_key("ucloud"),
            cooldown_seconds=5,
        )
        handler = MemoryHandler(json.dumps({"input": []}).encode(), path="/ucloud/v1/responses")
        admit = mocker.patch.object(admission, "admit_response")
        open_ = mocker.patch.object(upstream_exchange, "urlopen_direct")
        responses.relay(handler, "POST", PROVIDERS)

        admit.assert_not_called()
        open_.assert_not_called()
        assert handler.statuses == [429]
        assert b"provider_rate_limit_cooldown" in handler.output()

    def test_direct_relay_handles_large_request_and_cooldown(self, *, mocker) -> None:
        body = json.dumps({"input": "x" * 400_000}).encode()
        handler = MemoryHandler(body)
        mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            return_value=DirectResponse(b'{"id":"resp_large","status":"completed"}'),
        )
        responses.relay(handler, "POST", PROVIDERS)
        assert handler.statuses == [200]

        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        body = json.dumps({"input": []}).encode()
        handler = MemoryHandler(body)
        mocker.patch.object(cooldown, "remaining", return_value=1.0)
        responses.relay(handler, "POST", PROVIDERS)
        assert handler.statuses == [503]
        assert b"dmx_empty_response_exhausted" in handler.output()

    def test_direct_relay_exhausts_transport_retries(self, *, mocker) -> None:
        body = json.dumps({"input": []}).encode()
        handler = MemoryHandler(body)
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        open_ = mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            side_effect=OSError("fixture transport failure"),
        )
        sleep = mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        responses.relay(handler, "POST", PROVIDERS)

        assert open_.call_count == upstream_exchange._MAX_ATTEMPTS
        assert sleep.call_count == upstream_exchange._MAX_ATTEMPTS - 1
        assert handler.statuses == [502]
        assert b"upstream_transport_error" in handler.output()

    def test_direct_transport_terminal_branches_emit_bounded_results(self, *, mocker) -> None:
        handler = MemoryHandler()
        exchange = mocker.Mock(
            handler=handler,
            is_responses=True,
            used_input_variant_dialogue=False,
        )
        exchange.profile.wire_policy = None

        with pytest.raises(RuntimeError, match="wire recovery requires a provider policy"):
            upstream_exchange._reject_wire_failure(exchange, "fingerprint", 2, "event", "")
        assert not upstream_exchange._retry_wire_failure(exchange)

        sleep = mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        assert upstream_exchange._transport_error(exchange, OSError("private"), 0) == "retry"
        sleep.assert_called_once()

        outcome = upstream_exchange._transport_error(exchange, OSError("private"), 3)
        assert outcome == "terminal"
        assert handler.statuses == [502]
        assert b"upstream_transport_error" in handler.output()
