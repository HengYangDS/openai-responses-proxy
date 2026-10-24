"""Exchanges reuse one keep-alive connection per upstream address and never replay a sent request."""

from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import override

import pytest

from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import upstream as upstream_transport

OK = b'{"ok":true}'
type Reply = tuple[int, bytes, dict[str, str]]


class _Upstream:
    """Record every accepted connection with the headers of each request it carried."""

    def __init__(
        self,
        replies: list[Reply],
        *,
        drop_first: bool,
        hold: threading.Barrier | None,
        stall_second: threading.Event | None,
    ) -> None:
        self.connections: list[list[dict[str, str]]] = []
        self.dropped = threading.Event()
        lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            @override
            def log_message(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            @override
            def setup(self) -> None:
                super().setup()
                self.requests: list[dict[str, str]] = []
                with lock:
                    outer.connections.append(self.requests)

            def _reply(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with lock:
                    self.requests.append(
                        {key.lower(): value for key, value in self.headers.items()}
                    )
                    index = sum(map(len, outer.connections))
                    status, body, headers = replies.pop(0) if replies else (200, OK, {})
                if hold is not None and index <= 2:
                    hold.wait()
                if stall_second is not None and index == 2:
                    stall_second.wait(timeout=10)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
                if drop_first and index == 1:
                    # Close without announcing it, as an upstream does when an idle
                    # keep-alive connection reaches its own timeout.
                    self.close_connection = True

            def do_GET(self) -> None:
                self._reply()

            def do_POST(self) -> None:
                self._reply()

        class Server(ThreadingHTTPServer):
            @override
            def shutdown_request(
                self, request: socket.socket | tuple[bytes, socket.socket]
            ) -> None:
                super().shutdown_request(request)
                outer.dropped.set()

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/models"


@contextmanager
def _upstream(
    replies: list[Reply] | None = None,
    *,
    drop_first: bool = False,
    hold: threading.Barrier | None = None,
    stall_second: threading.Event | None = None,
) -> Generator[_Upstream]:
    upstream = _Upstream(
        list(replies or []), drop_first=drop_first, hold=hold, stall_second=stall_second
    )
    thread = threading.Thread(target=upstream.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield upstream
    finally:
        upstream_transport.reset_for_test()
        upstream.server.shutdown()
        upstream.server.server_close()
        thread.join(timeout=5)


def _open(url: str, *, data: bytes | None = None, budget: float = 5.0) -> object:
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    return upstream_exchange.urlopen_direct(request, budget, deadline=time.monotonic() + budget)


def _get(url: str) -> bytes:
    response = upstream_exchange.urlopen_direct(
        urllib.request.Request(url), 5, deadline=time.monotonic() + 5
    )
    try:
        return response.read()
    finally:
        response.close()


def _shape(upstream: _Upstream) -> list[int]:
    return [len(requests) for requests in upstream.connections]


class TestConnectionReuse:
    def setup_method(self) -> None:
        upstream_transport.reset_for_test()

    def teardown_method(self) -> None:
        upstream_transport.reset_for_test()

    def test_sequential_exchanges_share_one_connection(self):
        with _upstream() as upstream:
            assert [_get(upstream.url) for _ in range(3)] == [OK, OK, OK]
        assert _shape(upstream) == [3]

    def test_requests_keep_urllib_headers_except_the_forced_close(self):
        with _upstream() as upstream:
            request = urllib.request.Request(
                upstream.url,
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Bearer fixture"},
            )
            response = upstream_exchange.urlopen_direct(request, 5, deadline=time.monotonic() + 5)
            response.read()
            response.close()
        headers = upstream.connections[0][0]
        assert "connection" not in headers
        assert headers["host"] == upstream.url.split("/")[2]
        assert (
            headers["user-agent"]
            == f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}"
        )
        assert headers["accept-encoding"] == "identity"
        assert headers["content-type"] == "application/json"
        assert headers["content-length"] == "2"
        assert headers["authorization"] == "Bearer fixture"

    def test_an_announced_close_and_a_partial_read_are_not_reused(self):
        replies: list[Reply] = [(200, OK, {"Connection": "close"}), (200, b"x" * 65536, {})]
        with _upstream(replies) as upstream:
            assert _get(upstream.url) == OK
            response = upstream_exchange.urlopen_direct(
                urllib.request.Request(upstream.url), 5, deadline=time.monotonic() + 5
            )
            assert response.read(16) == b"x" * 16
            response.close()
            assert _get(upstream.url) == OK
        assert _shape(upstream) == [1, 1, 1]

    def test_a_connection_the_upstream_closed_while_idle_is_replaced_without_replay(self):
        with _upstream(drop_first=True) as upstream:
            assert _get(upstream.url) == OK
            assert upstream.dropped.wait(timeout=5)
            time.sleep(0.05)
            assert _get(upstream.url) == OK
        assert _shape(upstream) == [1, 1]

    def test_an_http_error_body_stays_readable_and_its_connection_is_reused(self):
        with _upstream([(429, b'{"error":"busy"}', {})]) as upstream:
            with pytest.raises(urllib.error.HTTPError) as caught:
                _open(upstream.url)
            assert caught.value.code == 429
            assert caught.value.read() == b'{"error":"busy"}'
            caught.value.close()
            assert _get(upstream.url) == OK
        assert _shape(upstream) == [2]

    def test_a_send_failure_on_a_reused_connection_retries_once_on_a_new_one(self, *, mocker):
        original = http.client.HTTPConnection.send
        failures: list[object] = []

        def send(connection: http.client.HTTPConnection, data: bytes) -> None:
            if not failures:
                failures.append(connection)
                raise BrokenPipeError("fixture reset")
            original(connection, data)

        with _upstream() as upstream:
            assert _get(upstream.url) == OK
            mocker.patch.object(http.client.HTTPConnection, "send", send)
            assert _get(upstream.url) == OK
        assert len(failures) == 1
        assert _shape(upstream) == [1, 1]

    def test_a_send_failure_on_a_new_connection_is_not_retried(self, *, mocker):
        attempts: list[object] = []

        def send(connection: http.client.HTTPConnection, data: bytes) -> None:
            del data
            attempts.append(connection)
            raise BrokenPipeError("fixture reset")

        with _upstream() as upstream:
            mocker.patch.object(http.client.HTTPConnection, "send", send)
            with pytest.raises(urllib.error.URLError):
                _get(upstream.url)
        assert len(attempts) == 1

    def test_concurrent_exchanges_never_share_a_connection(self):
        with _upstream(hold=threading.Barrier(2, timeout=5)) as upstream:
            bodies: list[bytes] = []
            threads = [
                threading.Thread(target=lambda: bodies.append(_get(upstream.url))) for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            assert bodies == [OK, OK]
            assert _get(upstream.url) == OK
        assert sorted(_shape(upstream)) == [1, 2]

    def test_an_expired_idle_connection_is_not_reused(self, *, mocker):
        mocker.patch("openai_responses_proxy.relay.connections.IDLE_SECONDS", 0.0)
        with _upstream() as upstream:
            assert _get(upstream.url) == OK
            time.sleep(0.01)
            assert _get(upstream.url) == OK
        assert _shape(upstream) == [1, 1]

    def test_a_reused_connection_still_ends_at_its_exchange_deadline(self):
        released = threading.Event()
        with _upstream(stall_second=released) as upstream:
            assert _get(upstream.url) == OK
            started = time.monotonic()
            try:
                with pytest.raises(upstream_transport.ExchangeDeadlineError):
                    _open(upstream.url, budget=0.3)
            finally:
                elapsed = time.monotonic() - started
                released.set()
        assert elapsed < 1.5
        assert _shape(upstream) == [2]
