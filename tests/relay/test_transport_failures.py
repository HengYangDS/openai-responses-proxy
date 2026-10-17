"""Bounded terminal relay and response contracts."""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.request
from pathlib import Path
from typing import Protocol
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import http_error

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = provider_registry.load()


class _OpenerWithHandlers(Protocol):
    handlers: list[urllib.request.BaseHandler]


class _HTTPSHandlerWithContext(Protocol):
    _context: ssl.SSLContext


class TestTransportFailures(InputTransportFixture):
    def test_upstream_tls_context_uses_the_packaged_trust_store(self, *, mocker) -> None:
        context = object()
        mocker.patch.object(upstream_exchange.certifi, "where", return_value="/safe/cacert.pem")
        create = mocker.patch.object(
            upstream_exchange.ssl, "create_default_context", return_value=context
        )

        assert upstream_exchange._upstream_tls_context() is context
        create.assert_called_once_with(cafile="/safe/cacert.pem")

    def test_direct_opener_carries_one_verifying_context_with_loaded_authorities(
        self,
    ) -> None:
        contexts = [
            cast(_HTTPSHandlerWithContext, handler)._context
            for handler in cast(_OpenerWithHandlers, upstream_exchange._DIRECT_OPENER).handlers
            if isinstance(handler, urllib.request.HTTPSHandler)
        ]

        assert len(contexts) == 1
        context = contexts[0]
        assert context.verify_mode is upstream_exchange.ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.cert_store_stats()["x509_ca"] > 0

    def test_direct_relay_covers_transport_exhaustion_without_a_local_queue(
        self, *, mocker
    ) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        handler = MemoryHandler(path="/dmxapi/v1/models")
        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=OSError("private"))
        responses.relay(handler, "GET", PROVIDERS)
        assert handler.statuses == [502]
        assert b"catalog_transport_error" in handler.output()

    def test_direct_relay_covers_retry_and_fallback_transport_failure(self, *, mocker) -> None:
        transient = http_error(503, "failure", b"temporary")
        success = DirectResponse(b'{"id":"resp_ok","status":"completed"}')
        handler = MemoryHandler(json.dumps({"input": []}).encode())
        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=[transient, success])
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        responses.relay(handler, "POST", PROVIDERS)
        assert handler.statuses == [200]
        assert b"resp_ok" in handler.output()
        assert cooldown.remaining(cooldown.unavailable_key("dmxapi")) == 0

        empty = (
            b'{"error":{"message":"official provider returned an empty response",'
            b'"type":"dmx_api_error","code":"empty_response"}}'
        )
        fallback_error = OSError("private")
        handler = MemoryHandler(json.dumps({"input": []}).encode())
        upstream_error = http_error(477, "empty", empty)
        mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            side_effect=[upstream_error, fallback_error],
        )
        responses.relay(handler, "POST", PROVIDERS)
        assert handler.statuses == [503]
        assert b"dmx_empty_response_exhausted" in handler.output()

    @pytest.mark.parametrize("provider", ["dmxapi", "ucloud", "aihubmix"])
    def test_exhausted_503_cools_only_the_failed_provider(self, provider: str, *, mocker) -> None:
        payload = b'{"error":{"type":"server_error","code":"overloaded"}}'
        failures = [http_error(503, "unavailable", payload) for _ in range(4)]
        other_success = DirectResponse(b'{"id":"resp_other","status":"completed"}')
        recovered = DirectResponse(b'{"id":"resp_recovered","status":"completed"}')
        open_ = mocker.patch.object(
            upstream_exchange, "urlopen_direct", side_effect=[*failures, other_success, recovered]
        )
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        now = [100.0]
        mocker.patch.object(cooldown.time, "monotonic", side_effect=lambda: now[0])
        body = json.dumps({"input": []}).encode()
        peer = "ucloud" if provider != "ucloud" else "aihubmix"
        first = MemoryHandler(body, path=f"/{provider}/v1/responses")
        second = MemoryHandler(body, path=f"/{provider}/v1/responses")
        other = MemoryHandler(body, path=f"/{peer}/v1/responses")
        after_expiry = MemoryHandler(body, path=f"/{provider}/v1/responses")

        responses.relay(first, "POST", PROVIDERS)
        responses.relay(second, "POST", PROVIDERS)
        responses.relay(other, "POST", PROVIDERS)
        now[0] += 5.0
        responses.relay(after_expiry, "POST", PROVIDERS)

        assert first.statuses == [503]
        assert first.output() == payload
        assert second.statuses == [503]
        assert b"provider_unavailable_cooldown" in second.output()
        assert ("Retry-After", "5") in second.sent_headers
        assert other.statuses == [200]
        assert b"resp_other" in other.output()
        assert after_expiry.statuses == [200]
        assert b"resp_recovered" in after_expiry.output()
        assert open_.call_count == 6

    def test_response_failed_recovery_stops_after_dialogue_and_transport_retries(
        self, *, mocker
    ) -> None:
        exchange = mocker.Mock(
            is_responses=True,
            response_failed_stages=upstream_exchange.RESPONSE_FAILED_MAX_STAGES,
            used_response_failed_dialogue=True,
        )
        assert not upstream_exchange._recover_response_failed(exchange, 400, "full")

        handler = MemoryHandler(json.dumps({"input": []}).encode())
        exchange = upstream_exchange.Exchange(
            handler,
            "POST",
            1,
            b"{}",
            "https://upstream.test/v1/responses",
            {},
            True,
            b"{}",
            PROVIDERS.profiles["dmxapi"],
        )
        mocker.patch.object(upstream_exchange, "_transport_error", return_value="retry")
        mocker.patch.object(
            upstream_exchange.Exchange,
            "upstream",
            side_effect=OSError("retry"),
        )
        mocker.patch.object(upstream_exchange, "_MAX_ATTEMPTS", 1)
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 0)
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_DIALOGUE_SLOTS", 0)
        mocker.patch.object(upstream_exchange, "INPUT_VARIANT_DIALOGUE_SLOTS", 0)
        assert upstream_exchange.open_upstream(exchange) is None

    def test_responses_relay_dispatches_non_responses_body_and_empty_note(self, *, mocker) -> None:
        handler = MemoryHandler(b"", path="/dmxapi/v1/models")
        response = DirectResponse(b'{"data":[]}')
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=response)
        responses.relay(handler, "GET", PROVIDERS)
        assert b'"data":[]' in handler.output()

        handler = MemoryHandler(json.dumps({"input": []}).encode())
        projected = rewrite.ProjectionResult(b'{"input":[],"store":false}', "clean")
        mocker.patch.object(rewrite, "sanitize_responses_body", return_value=projected)
        mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            return_value=DirectResponse(b'{"status":"completed","output":[]}'),
        )
        responses.relay(handler, "POST", PROVIDERS)
        assert handler.statuses == [200]

    def test_direct_non_responses_body_covers_empty_and_terminal_chunks(self, *, mocker) -> None:
        handler = MemoryHandler(path="/dmxapi/v1/health")
        exchange = mocker.Mock(handler=handler, is_responses=True)
        exchange.input_variant_accepted = mocker.Mock()
        downstream.relay_body(exchange, DirectResponse(b"", b""))
        assert handler.statuses == [200]
        assert handler.output() == b"0\r\n\r\n"
        exchange.input_variant_accepted.assert_called_once_with()

        admission.reset_for_test()
        telemetry.reset_for_test()
        handler = MemoryHandler(path="/dmxapi/v1/health")
        exchange = mocker.Mock(handler=handler, is_responses=True)
        exchange.input_variant_accepted = mocker.Mock()
        downstream.relay_body(
            exchange,
            DirectResponse(b"partial", http.client.IncompleteRead(b"terminal")),
        )
        assert b"partial" in handler.output()
        assert b"terminal" in handler.output()
        counters = cast("dict[str, int]", self._status_snapshot()["counters"])
        assert counters["responses_completed"] == 1
        exchange.input_variant_accepted.assert_called_once_with()

    def test_non_responses_helpers_bypass_responses_only_policy(self, *, mocker) -> None:
        handler = MemoryHandler(path="/dmxapi/v1/models")
        exchange = mocker.Mock(handler=handler, is_responses=False)
        exchange.input_variant_accepted = mocker.Mock()

        assert responses._admit(exchange)
        assert not responses._cooldown_active(exchange)
        downstream.relay_body(exchange, DirectResponse(b""))
        assert handler.output() == b"0\r\n\r\n"
        exchange.input_variant_accepted.assert_called_once_with()

    def test_direct_non_sse_stream_handles_incomplete_and_writer_failures(self, *, mocker) -> None:
        partial = http.client.IncompleteRead(b"partial")
        handler = MemoryHandler(path="/dmxapi/v1/models")
        mocker.patch.object(
            upstream_exchange, "urlopen_direct", return_value=DirectResponse(partial)
        )
        responses.relay(handler, "GET", PROVIDERS)
        assert handler.statuses == [200]
        assert b"partial" in handler.output()

        statuses = []
        for error in (BrokenPipeError(), RuntimeError("private")):
            handler = MemoryHandler(path="/dmxapi/v1/models")
            handler.wfile = mocker.Mock()
            handler.wfile.write.side_effect = error
            mocker.patch.object(
                upstream_exchange,
                "urlopen_direct",
                return_value=DirectResponse(b"body"),
            )
            responses.relay(handler, "GET", PROVIDERS)
            statuses.append(handler.statuses)
        assert statuses == [[200], [200]]

    def test_non_stream_responses_preserve_ciphertext_for_current_turn_decryption(
        self, *, mocker
    ) -> None:
        upstream = json.dumps(
            {
                "id": "resp_provider_bound",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "encrypted_content": "reasoning-secret"},
                    {
                        "type": "agent_message",
                        "content": [
                            {
                                "type": "encrypted_content",
                                "encrypted_content": "agent-secret",
                            },
                            {"type": "output_text", "text": "visible"},
                        ],
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()
        handler = MemoryHandler(json.dumps({"input": []}).encode())
        mocker.patch.object(
            upstream_exchange, "urlopen_direct", return_value=DirectResponse(upstream)
        )
        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [200]
        assert b"reasoning-secret" in handler.output()
        assert b"agent-secret" in handler.output()
        assert b"visible" in handler.output()

    def test_empty_responses_request_fails_before_upstream_io(self, *, mocker) -> None:
        handler = MemoryHandler(b"")
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct")
        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [400]
        assert b"provider_portable_projection_rejected" in handler.output()
        opened.assert_not_called()

    def test_invalid_successful_responses_fail_before_downstream_commit(
        self, subtests, *, mocker
    ) -> None:
        invalid = (
            DirectResponse(b""),
            DirectResponse(http.client.IncompleteRead(b'{"status":"completed"')),
            DirectResponse(b"not-json"),
            DirectResponse(b'{"id":"resp","status":"in_progress","output":[]}'),
        )
        for upstream in invalid:
            with subtests.test(upstream=upstream):
                admission.reset_for_test()
                handler = MemoryHandler(json.dumps({"input": []}).encode())
                mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=upstream)
                responses.relay(handler, "POST", PROVIDERS)
                assert handler.statuses == [503]
                assert b"invalid_responses_success_body" in handler.output()

    def test_oversized_successful_response_fails_before_downstream_commit(self, *, mocker) -> None:
        handler = MemoryHandler(json.dumps({"input": []}).encode())
        upstream = DirectResponse(b'{"status":"completed","output":"', b"x" * 64, b'"}')
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=upstream)
        mocker.patch.object(downstream, "MAX_RESPONSES_JSON_BYTES", 32)
        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [503]
        assert b"response_too_large" in handler.output()
