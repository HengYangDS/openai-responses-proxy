"""Bounded terminal relay and response contracts."""

from __future__ import annotations

import http.client
import json
import socket
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
from openai_responses_proxy.runtime import config
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

    @pytest.mark.parametrize(
        ("stage", "failure"),
        [
            ("headers", BrokenPipeError()),
            ("headers", ConnectionResetError()),
            ("headers", RuntimeError("private header failure")),
            ("body", BrokenPipeError()),
            ("read", OSError("private upstream failure")),
        ],
    )
    def test_catalog_failure_closes_the_real_upstream_response(
        self, stage: str, failure: Exception, *, mocker
    ) -> None:
        handler = MemoryHandler(path="/dmxapi/v1/models")
        upstream_socket, peer = socket.socketpair()
        with upstream_socket, peer:
            upstream_socket.settimeout(1)
            peer.settimeout(1)
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            peer.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 9000\r\n\r\n" + b"x" * 9000
            )
            with http.client.HTTPResponse(upstream_socket) as response:
                response.begin()
                closed = mocker.spy(response, "close")
                opened = mocker.patch.object(
                    upstream_exchange, "open_readonly", return_value=response
                )
                match stage:
                    case "headers":
                        mocker.patch.object(handler, "end_headers", side_effect=failure)
                    case "body":
                        mocker.patch.object(handler.wfile, "write", side_effect=failure)
                    case "read":
                        mocker.patch.object(response, "read", side_effect=failure)

                responses.relay(handler, "GET", PROVIDERS)

                assert response.isclosed()
                closed.assert_called_once_with()
                assert handler.statuses == [200]
                assert b"private" not in handler.output()
                opened.assert_called_once()
                counters, classifications = self._status_maps()
                assert counters.get("responses_received", 0) == 0
                assert classifications == {}

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

    @pytest.mark.parametrize(
        ("body", "length", "expected", "disconnected"),
        [
            (b"", 0, b"0\r\n\r\n", False),
            (b"complete", 8, b"8\r\ncomplete\r\n0\r\n\r\n", False),
            (b"partial", 16, b"7\r\npartial\r\n", True),
        ],
        ids=["empty", "complete", "truncated"],
    )
    def test_catalog_terminates_only_a_complete_upstream_body(
        self, body: bytes, length: int, expected: bytes, *, disconnected: bool, mocker
    ) -> None:
        handler = MemoryHandler(path="/dmxapi/v1/models")
        handler.close_connection = False
        upstream_socket, peer = socket.socketpair()
        with upstream_socket, peer:
            upstream_socket.settimeout(1)
            peer.settimeout(1)
            peer.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {length}\r\n\r\n".encode()
                + body
            )
            peer.shutdown(socket.SHUT_WR)
            with http.client.HTTPResponse(upstream_socket) as response:
                response.begin()
                closed = mocker.spy(response, "close")
                mocker.patch.object(upstream_exchange, "open_readonly", return_value=response)

                responses.relay(handler, "GET", PROVIDERS)

                assert response.isclosed()
                closed.assert_called_once_with()

        assert handler.statuses == [200]
        assert handler.output() == expected
        assert handler.close_connection is disconnected
        counters, classifications = self._status_maps()
        assert counters.get("responses_completed", 0) == 0
        assert classifications == {}

    def test_non_responses_admission_bypasses_responses_only_policy(self, *, mocker) -> None:
        handler = MemoryHandler(path="/dmxapi/v1/models")
        exchange = mocker.Mock(handler=handler, is_responses=False)
        assert responses._admit(exchange)
        assert not responses._cooldown_active(exchange)

    def test_direct_non_sse_stream_handles_incomplete_and_writer_failures(self, *, mocker) -> None:
        partial = http.client.IncompleteRead(b"partial")
        handler = MemoryHandler(path="/dmxapi/v1/models")
        mocker.patch.object(
            upstream_exchange, "urlopen_direct", return_value=DirectResponse(partial)
        )
        responses.relay(handler, "GET", PROVIDERS)
        assert handler.statuses == [200]
        assert handler.output() == b"7\r\npartial\r\n"
        assert handler.close_connection

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

    @pytest.mark.parametrize(
        ("ticks", "consumed"),
        [([0.0, 1.1], 0), ([0.0, 0.1, 1.1], 7)],
        ids=["expired-before-read", "expired-on-completion"],
    )
    def test_body_deadline_rejects_expiry_and_restores_socket_timeout(
        self, ticks: list[float], consumed: int, *, mocker
    ) -> None:
        handler = MemoryHandler(b"private")
        handler.connection.settimeout(0.25)
        settings = config.load({config.REQUEST_BODY_TIMEOUT_ENV: "1"})
        mocker.patch.object(responses, "_SETTINGS", settings)
        mocker.patch.object(responses.time, "monotonic", side_effect=ticks)

        assert responses._read_responses_body(handler) == (None, "request_body_timeout")
        assert handler.rfile.tell() == consumed
        assert handler.connection.gettimeout() == 0.25

    @pytest.mark.parametrize("previous", [None, 0.25, 2.0])
    def test_body_read_clamps_and_restores_the_original_socket_timeout(
        self, previous: float | None, *, mocker
    ) -> None:
        handler = MemoryHandler(b"complete")
        handler.connection.settimeout(previous)
        settings = config.load({config.REQUEST_BODY_TIMEOUT_ENV: "1"})
        mocker.patch.object(responses, "_SETTINGS", settings)
        mocker.patch.object(responses.time, "monotonic", return_value=0.0)
        armed = mocker.spy(handler.connection, "settimeout")

        assert responses._read_responses_body(handler) == (b"complete", "")
        assert armed.call_args_list == [
            mocker.call(1.0 if previous is None else min(previous, 1.0)),
            mocker.call(previous),
        ]
        assert handler.connection.gettimeout() == previous

    @pytest.mark.parametrize(
        ("failure", "status", "reason"),
        [
            (TimeoutError("private timeout"), 408, "request_body_timeout"),
            (OSError("private read failure"), 400, "request_body_unreadable"),
        ],
    )
    def test_failed_body_reads_reject_before_upstream_without_disclosure(
        self, failure: OSError, status: int, reason: str, *, mocker
    ) -> None:
        handler = MemoryHandler(b"private body")
        handler.connection.settimeout(0.25)
        mocker.patch.object(handler.rfile, "read1", side_effect=failure)
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct")

        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [status]
        assert ("Connection", "close") in handler.sent_headers
        assert handler.connection.gettimeout() == 0.25
        error = json.loads(handler.output())["error"]
        assert error["code"] == "request_body_rejected"
        assert error["reason"] == reason
        assert b"private" not in handler.output()
        opened.assert_not_called()

    def test_unparseable_body_length_fails_without_reading_or_upstream_io(self, *, mocker) -> None:
        handler = MemoryHandler(b"private body")
        del handler.headers["Content-Length"]
        handler.headers["Content-Length"] = "9" * 5000
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct")

        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [400]
        assert handler.rfile.tell() == 0
        assert ("Connection", "close") in handler.sent_headers
        assert json.loads(handler.output())["error"]["reason"] == "invalid_content_length"
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
