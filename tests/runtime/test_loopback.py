"""Loopback HTTP transport isolation."""

from __future__ import annotations

import ssl
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING
from typing import override

from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.runtime import loopback

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_loopback_transport_does_not_initialize_https(*, mocker: MockerFixture) -> None:
    """Keep local control-plane requests independent of TLS support."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        @override
        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mocker.patch.object(
        ssl, "create_default_context", side_effect=AssertionError("TLS initialized")
    )
    try:
        request = control.urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/healthz",
            method="GET",
        )
        with loopback.open_request(request, timeout_seconds=1) as response:
            assert response.status == 200
            assert response.read() == b'{"ok":true}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
