"""Direct HTTP transport for the product's loopback-only control plane."""

from __future__ import annotations

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


def opener() -> urllib.request.OpenerDirector:
    """Build an HTTP-only, proxy-free opener without initializing TLS."""
    direct = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        direct.add_handler(handler)
    return direct


def open_request(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
) -> AbstractContextManager[Response]:
    """Open one direct HTTP request to the loopback control plane."""
    response = cast("Response", opener().open(request, timeout=timeout_seconds))
    return closing(response)
