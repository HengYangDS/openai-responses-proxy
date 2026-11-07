"""Model catalog requests spend one bounded deadline across lookup, headers, body and writes."""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
import urllib.error
from typing import override

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import upstream as upstream_transport
from openai_responses_proxy.runtime import config
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import TimedResponse
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import proxy_is_up


class TestCatalogDeadline(InputTransportFixture):
    @pytest.mark.parametrize("deadline", [100.0, 99.0], ids=["at-expiry", "after-expiry"])
    def test_expired_catalog_budget_never_starts_upstream_lookup(self, deadline, *, mocker):
        mocker.patch.object(time, "monotonic", return_value=100.0)
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct")

        with pytest.raises(upstream_transport.ExchangeDeadlineError):
            upstream_exchange.open_readonly(
                "https://upstream.test/models", "GET", {}, deadline=deadline
            )

        opened.assert_not_called()

    def test_catalog_stalled_native_writer_cannot_hold_upstream_past_the_deadline(self, *, mocker):
        handler = MemoryHandler(path="/dmxapi/v1/models")
        handler.close_connection = False
        upstream = DirectResponse(*([b"x" * 8192] * 32))
        closed = mocker.spy(upstream, "close")
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=upstream)
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.04"})
        )
        connection, peer = socket.socketpair()

        class StalledWriter(socketserver.StreamRequestHandler):
            @override
            def handle(self) -> None:
                handler.connection = self.connection
                handler.wfile = self.wfile
                responses.relay(handler, "GET", provider_registry.load())

        with connection, peer:
            connection.settimeout(0.3)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            with socketserver.TCPServer(("127.0.0.1", 0), StalledWriter) as native_server:
                started = time.monotonic()

                StalledWriter(connection, ("127.0.0.1", 0), native_server)

                assert time.monotonic() - started < 0.2
                assert handler.statuses == [200]
                assert handler.close_connection
                assert connection.gettimeout() == 0.3
                closed.assert_called_once_with()
        assert connection.fileno() == peer.fileno() == -1

    def test_catalog_late_header_transport_failure_has_a_deadline_verdict(self, *, mocker):
        now = [100.0]
        mocker.patch.object(time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.5"})
        )

        def open_headers(_request, timeout, *, deadline=None):
            del timeout, deadline
            now[0] += 0.6
            raise OSError("private expired transport error")

        opened = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(path="/dmxapi/v1/models")

        responses.relay(handler, "GET", provider_registry.load())

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "catalog_deadline_exceeded"
        assert b"private" not in handler.output()
        opened.assert_called_once()

    def test_catalog_local_failure_writer_has_a_finite_recovery_budget(self, *, mocker):
        mocker.patch.object(time, "monotonic", return_value=100.0)
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.04"})
        )
        opened = mocker.patch.object(
            upstream_exchange, "urlopen_direct", side_effect=OSError("private transport")
        )
        handler = MemoryHandler(path="/dmxapi/v1/models")
        handler.close_connection = False
        observed = []

        def write(_payload):
            observed.append(handler.connection.gettimeout())
            raise TimeoutError("private downstream timeout")

        mocker.patch.object(handler.wfile, "write", side_effect=write)

        responses.relay(handler, "GET", provider_registry.load())

        assert handler.statuses == [502]
        assert observed == [pytest.approx(0.04)]
        assert handler.connection.gettimeout() is None
        assert handler.close_connection
        opened.assert_called_once()

    @pytest.mark.parametrize("phase", ["headers", "body"])
    @pytest.mark.parametrize("status", [200, 403])
    def test_catalog_native_framing_cannot_renew_its_deadline(self, phase, status, *, mocker):
        upstream = ScriptedUpstream()
        release = threading.Event()
        finished = threading.Event()

        def slow_framing(handler):
            try:
                prefix = f"HTTP/1.1 {status} Reply\r\n".encode()
                prefix += (
                    b"X-Progress: "
                    if phase == "headers"
                    else b"Transfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n"
                    b"4\r\nkeep\r\n2;progress="
                )
                handler.connection.sendall(prefix)
                for _ in range(16):
                    if release.wait(timeout=0.08):
                        return
                    handler.connection.sendall(b"x")
                handler.connection.sendall(
                    b"\r\nContent-Length: 0\r\n\r\n"
                    if phase == "headers"
                    else b"\r\n{}\r\n0\r\n\r\n"
                )
            finally:
                finished.set()

        upstream.push(slow_framing)
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", upstream.base_url() + "/v1")}
        )
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.25"})
        )
        handler = MemoryHandler(path="/fixture/v1/models")
        handler.close_connection = False
        upstream.start()
        try:
            started = time.monotonic()

            responses.relay(handler, "GET", providers)

            assert time.monotonic() - started < 0.65
            if phase == "headers":
                assert handler.statuses == [504]
                assert json.loads(handler.output())["error"]["code"] == "catalog_deadline_exceeded"
            else:
                assert handler.statuses == [status]
                assert handler.output() == b"4\r\nkeep\r\n"
                assert handler.close_connection
            assert len(upstream.received) == 1
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            release.set()
            upstream.close()
            assert finished.wait(timeout=2)
            assert not upstream.thread.is_alive()
            assert not proxy_is_up(upstream.port)

    @pytest.mark.parametrize("status", [200, 403])
    def test_catalog_body_cannot_refund_time_spent_acquiring_headers(self, status, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.5"})
        )
        upstream = TimedResponse(now, [b"private-body"] * 3, "application/json")
        upstream.status = status

        def open_headers(_request, timeout, *, deadline=None):
            del timeout, deadline
            now[0] += 0.4
            if status >= 400:
                raise urllib.error.HTTPError(
                    "https://upstream.test/models", status, "denied", upstream.headers, upstream
                )
            return upstream

        opened = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(path="/dmxapi/v1/models")
        handler.close_connection = False

        responses.relay(handler, "GET", provider_registry.load())

        assert handler.statuses == [status]
        assert handler.output() == b""
        assert handler.close_connection
        assert upstream.closed
        opened.assert_called_once()
        assert now[0] <= 100.5
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize("status", [200, 403])
    def test_catalog_headers_arriving_after_expiry_cannot_commit(self, status, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.5"})
        )
        upstream = TimedResponse(now, [], "application/json")

        def open_headers(_request, timeout, *, deadline=None):
            del timeout, deadline
            now[0] += 0.6
            if status >= 400:
                raise urllib.error.HTTPError(
                    "https://upstream.test/models", status, "denied", upstream.headers, upstream
                )
            return upstream

        opened = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(path="/dmxapi/v1/models")

        responses.relay(handler, "GET", provider_registry.load())

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "catalog_deadline_exceeded"
        assert upstream.closed
        opened.assert_called_once()

    def test_catalog_writes_spend_the_remaining_deadline_and_restore_the_socket(self, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(
            responses, "_SETTINGS", config.load({"OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "0.5"})
        )
        upstream = TimedResponse(now, [b"keep", b"late"], "application/json")
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=upstream)
        handler = MemoryHandler(path="/dmxapi/v1/models")
        handler.close_connection = False
        original_write = handler.wfile.write
        budgets = []

        def write(payload):
            budget = handler.connection.gettimeout()
            budgets.append(budget)
            if budget is not None and budget < 0.2 - 1e-9:
                now[0] += budget
                raise TimeoutError("private downstream timeout")
            now[0] += 0.2
            return original_write(payload)

        mocker.patch.object(handler.wfile, "write", side_effect=write)

        responses.relay(handler, "GET", provider_registry.load())

        assert handler.statuses == [200]
        assert handler.output() == b"4\r\nkeep\r\n"
        assert handler.close_connection
        assert budgets == [pytest.approx(0.3)]
        assert handler.connection.gettimeout() is None
        assert upstream.closed
