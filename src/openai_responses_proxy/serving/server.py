"""Loopback HTTP server and route dispatch for OpenAI Responses Proxy.

The handler owns only method/path routing.  Upstream request orchestration and
lifecycle controls are delegated to their semantic owners through immutable
process bindings supplied by the executable entrypoint.
"""

from __future__ import annotations

import contextlib
import socket
import socketserver
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING
from typing import cast
from typing import override

from openai_responses_proxy import product_identity
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import responses
from openai_responses_proxy.serving import control

if TYPE_CHECKING:
    from openai_responses_proxy.providers import registry as provider_registry

type ServerRequest = socket.socket | tuple[bytes, socket.socket]
type ClientAddress = tuple[str, int] | str


@dataclass(frozen=True)
class Bindings:
    """Immutable process dependencies owned by one HTTP server instance."""

    control: control.Bindings
    providers: provider_registry.Registry
    server_version: str


class ResilientProxyServer(ThreadingHTTPServer):
    """Threading HTTP server hardened for concurrent local Responses streams."""

    request_queue_size = 256
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        bindings: Bindings,
        *,
        bind_and_activate: bool = True,
    ) -> None:
        """Bind immutable product dependencies to one listener instance."""
        self.bindings = bindings
        super().__init__(server_address, request_handler_class, bind_and_activate)

    @override
    def server_bind(self) -> None:
        """Bind the loopback listener without a blocking reverse-name lookup."""
        # This product is loopback-only.  HTTPServer.server_bind() performs a
        # reverse/FQDN lookup solely to populate presentation attributes; that
        # lookup can block listener admission on hosts with degraded DNS.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = cast("str", host)
        self.server_port = port
        _disable_nagle(self.socket)

    @override
    def get_request(self) -> tuple[socket.socket, object]:
        """Accept one connection and disable Nagle delay on its socket."""
        request, address = super().get_request()
        _disable_nagle(request)
        return request, address

    @override
    def process_request_thread(
        self,
        request: object,
        client_address: object,
    ) -> None:
        """Count one request handler while it runs."""
        admission.begin_handler()
        try:
            super().process_request_thread(
                cast("ServerRequest", request),
                cast("ClientAddress", client_address),
            )
        finally:
            admission.end_handler()

    @override
    def handle_error(self, request: object, client_address: object) -> None:
        """Name a request that its client closed, without a traceback.

        Only a request whose line has been read and whose response is not admitted arrives
        here. The handler ends a connection that its client closes between two requests, and
        the relay names a client's close of an admitted response under that request.
        """
        error = sys.exception()
        if isinstance(error, downstream.CLIENT_CLOSED):
            operational_log.log(
                f"event=client_closed_mid_request "
                f"exception={operational_log.safe_exception_label(error)}"
            )
            return
        super().handle_error(cast("ServerRequest", request), cast("ClientAddress", client_address))


def _disable_nagle(connection: socket.socket) -> None:
    with contextlib.suppress(OSError):
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class Handler(BaseHTTPRequestHandler):
    """Route loopback HTTP methods to transport and lifecycle owners."""

    protocol_version = "HTTP/1.1"
    server_version = f"{product_identity.PRODUCT_SLUG}/0+unconfigured"
    # Empty until a request line has been read on the current pass of the connection.
    raw_requestline: bytes = b""

    @override
    def version_string(self) -> str:
        """Return the immutable release identity bound to this server."""
        return cast("ResilientProxyServer", self.server).bindings.server_version

    def _bindings(self) -> Bindings:
        return cast("ResilientProxyServer", self.server).bindings

    @override
    def log_message(self, *args: object, **kwargs: object) -> None:
        """Suppress the base server's unstructured stderr access log."""
        del args, kwargs

    @override
    def handle_one_request(self) -> None:
        """Serve one request; end quietly a connection that its client closes between two.

        A client ends a kept-alive connection by a reset as readily as by an orderly close, and
        neither is a request. Once a request line has been read, the same error leaves the
        handler and the server names it.
        """
        try:
            super().handle_one_request()
        except downstream.CLIENT_CLOSED:
            if self.raw_requestline:
                raise
            self.close_connection = True
        finally:
            self.raw_requestline = b""

    def do_POST(self) -> None:
        """Route the handoff control request or relay one upstream request."""
        if self.path == "/control/handoff":
            control.prepare_handoff(self, self._bindings().control)
        else:
            responses.relay(self, "POST", self._bindings().providers)

    def do_GET(self) -> None:
        """Serve local health or relay one upstream GET request."""
        if self.path == "/healthz":
            control.send_status(self, self._bindings().control)
        else:
            responses.relay(self, "GET", self._bindings().providers)

    def do_DELETE(self) -> None:
        """Relay one upstream DELETE request."""
        responses.relay(self, "DELETE", self._bindings().providers)

    def do_PATCH(self) -> None:
        """Relay one upstream PATCH request."""
        responses.relay(self, "PATCH", self._bindings().providers)

    def do_PUT(self) -> None:
        """Relay one upstream PUT request."""
        responses.relay(self, "PUT", self._bindings().providers)


def server_from_listener(listener: socket.socket, bindings: Bindings) -> ResilientProxyServer:
    """Create a configured server around one already-bound handoff listener."""
    address = listener.getsockname()
    server = ResilientProxyServer(address, Handler, bindings, bind_and_activate=False)
    server.socket.close()
    server.socket = listener
    server.server_address = address
    server.server_name = cast("str", address[0])
    server.server_port = address[1]
    return server
