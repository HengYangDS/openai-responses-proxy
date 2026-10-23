"""Upstream redirects reach the client unfollowed, so its credential stays with the selected upstream."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import override

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import InputTransportFixture

CREDENTIAL = "Bearer redirect-target-must-not-see-this"


@contextmanager
def _redirecting_upstream(status: int) -> Generator[tuple[int, list[dict[str, object]]]]:
    """Serve a proxy whose only upstream redirects every request to a recording collector."""
    collected: list[dict[str, object]] = []

    class Collector(BaseHTTPRequestHandler):
        @override
        def log_message(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def _collect(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            collected.append(
                {
                    "method": self.command,
                    "authorization": self.headers.get("Authorization"),
                    "body": self.rfile.read(length),
                }
            )
            body = b'{"object":"list","data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._collect()

        def do_POST(self) -> None:
            self._collect()

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    target = f"http://127.0.0.1:{collector.server_address[1]}/collect"

    class Redirector(BaseHTTPRequestHandler):
        @override
        def log_message(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def _redirect(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b'{"error":{"message":"moved","type":"redirect"}}'
            self.send_response(status)
            self.send_header("Location", target)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._redirect()

        def do_POST(self) -> None:
            self._redirect()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
    providers = provider_registry.Registry(
        {
            "aihubmix": provider_registry.Profile(
                "aihubmix", f"http://127.0.0.1:{upstream.server_address[1]}"
            )
        }
    )
    server = proxy.create_server(("127.0.0.1", 0), providers=providers)
    threads = [
        threading.Thread(target=owned.serve_forever, daemon=True)
        for owned in (collector, upstream, server)
    ]
    for thread in threads:
        thread.start()
    try:
        yield server.server_address[1], collected
    finally:
        for owned in (server, upstream, collector):
            owned.shutdown()
            owned.server_close()
        for thread in threads:
            thread.join(timeout=2)


def _exchange(port: int, method: str, path: str, body: bytes) -> tuple[int, str]:
    """Send one request through the proxy with a client that never follows redirects."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Authorization": CREDENTIAL}
        if body:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body or None, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status, response.getheader("Location", "")
    finally:
        connection.close()


class TestUpstreamRedirects(InputTransportFixture):
    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    def test_catalog_redirect_reaches_the_client_unfollowed(self, status):
        with _redirecting_upstream(status) as (port, collected):
            reply, location = _exchange(port, "GET", "/aihubmix/v1/models", b"")

        assert collected == []
        assert reply == status
        assert location.endswith("/collect")

    @pytest.mark.parametrize("status", [301, 302, 303])
    def test_responses_redirect_reaches_the_client_unfollowed(self, status):
        body = json.dumps({"model": "gpt-5.6-terra", "stream": False, "input": []}).encode()
        with _redirecting_upstream(status) as (port, collected):
            reply, location = _exchange(port, "POST", "/aihubmix/v1/responses", body)

        assert collected == []
        assert reply == status
        assert location.endswith("/collect")
