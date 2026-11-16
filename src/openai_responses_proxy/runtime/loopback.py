"""Direct HTTP transport for the product's loopback-only control plane."""

from __future__ import annotations

import http.client
import socket
import struct
import sys
import urllib.request
from contextlib import AbstractContextManager
from contextlib import closing
from typing import Protocol
from typing import cast


class Response(Protocol):
    """Response surface consumed by loopback control-plane clients."""

    status: int

    def read(self, amount: int | None = None) -> bytes:
        """Read response bytes, optionally bounded by ``amount``."""

    def close(self) -> None:
        """Release the underlying HTTP connection."""


def _opener(http: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """Build an HTTP-only, proxy-free opener that sends through ``http``."""
    direct = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        urllib.request.UnknownHandler(),
        http,
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        direct.add_handler(handler)
    return direct


def opener() -> urllib.request.OpenerDirector:
    """Build an HTTP-only, proxy-free opener without initializing TLS."""
    return _opener(urllib.request.HTTPHandler())


def open_request(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
) -> AbstractContextManager[Response]:
    """Open one direct HTTP request to the loopback control plane."""
    response = cast("Response", opener().open(request, timeout=timeout_seconds))
    return closing(response)


def reset_on_close() -> bytes:
    """Return the value of ``SO_LINGER`` that makes every close of a socket a reset.

    Lingering is on, with zero seconds. Windows packs the two fields as unsigned shorts and
    every other platform as ints.
    """
    return struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)


def connect_reset_on_close(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
) -> http.client.HTTPConnection:
    """Open the connection of one request, and make every close of it a reset.

    Nothing of the request is written here, so a failure says that nothing was sent. From the
    return on, the connection ends with a reset however it ends: by its close after an answer,
    a timeout or an interrupt, and by the kernel when the process ends. A listener that does
    not have the connection in its queue by then never receives what was written on it; an
    ordinary close would leave the request to arrive later, behind a connection that was made
    after it. The option is never switched off, and the connection is never opened again.
    """
    connection = http.client.HTTPConnection(request.host, timeout=timeout_seconds)
    # A second connection would carry no option: sending on a closed one fails instead.
    connection.auto_open = 0
    try:
        # The connect can fail after its socket exists: a listener that has reset the
        # connection refuses the first option that the connect itself sets.
        connection.connect()
        connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, reset_on_close())
    except BaseException:
        # A request that cannot be withdrawn is not sent. Nothing was written: this close
        # leaves the listener a connection without a request.
        connection.close()
        raise
    return connection


class _OpenConnection(urllib.request.AbstractHTTPHandler):
    """Send a request on one connection that is already open, as the opener sends any other."""

    def __init__(self, connection: http.client.HTTPConnection) -> None:
        """Bind the handler to the connection of its one request."""
        super().__init__()
        self._connection = connection

    def http_open(self, request: urllib.request.Request) -> http.client.HTTPResponse:
        """Write the request on the bound connection and read the head of its response."""
        return self.do_open(lambda _host, **_options: self._connection, request)

    http_request = urllib.request.AbstractHTTPHandler.do_request_


def open_request_on(
    connection: http.client.HTTPConnection,
    request: urllib.request.Request,
) -> AbstractContextManager[Response]:
    """Send one direct HTTP request on an open connection and return its response.

    The request is written and answered as :func:`open_request` writes and answers it. The
    connection keeps the timeout with which it was opened, and it is closed when the response
    is left and when the sending or the reading fails.
    """
    response = cast("Response", _opener(_OpenConnection(connection)).open(request))
    return closing(response)
