"""Keep-alive reuse of upstream connections behind urllib's own request pipeline.

urllib's ``AbstractHTTPHandler.do_open`` builds each request's headers, wraps send errors and leaves non-2xx
statuses to ``HTTPError`` as before. It also forces ``Connection: close``, so every exchange used to pay for a new
TCP connection and TLS handshake. A connection returns to this pool only after its response was read to the end
without an announced close, and an idle one is reused only while it is fresh and its peer has not closed it.
"""

from __future__ import annotations

import http.client
import selectors
import ssl
import threading
import time
from collections.abc import Callable

IDLE_SECONDS = 30.0
IDLE_PER_ADDRESS = 8

type Address = tuple[str, str, int]


class Reusable(http.client.HTTPConnection):
    """A connection that its response hands back once the body was read to the end or abandoned."""

    _release: Callable[[], None] | None = None

    def lend(self, release: Callable[[], None]) -> None:
        """Arrange for the connection to go back to its pool when its next response completes."""
        self._release = release

    def finish(self, *, reusable: bool) -> None:
        """Return the connection to its pool when reusable, otherwise close it."""
        release, self._release = self._release, None
        if reusable and release is not None and self.sock is not None:
            release()
        else:
            self.close()


class Pool[C: Reusable]:
    """Idle connections per upstream address, most recently used first."""

    def __init__(self) -> None:
        """Start without idle connections."""
        self._lock = threading.Lock()
        self._idle: dict[Address, list[tuple[float, C]]] = {}

    def acquire(self, address: Address) -> C | None:
        """Return a usable idle connection to the address, closing expired or closed ones."""
        while True:
            with self._lock:
                idle = self._idle.get(address, [])
                if not idle:
                    return None
                since, connection = idle.pop()
            if time.monotonic() - since <= IDLE_SECONDS and _idle_and_open(connection):
                return connection
            connection.close()

    def release(self, address: Address, connection: C) -> None:
        """Keep a connection whose response completed, within the per-address bound."""
        with self._lock:
            idle = self._idle.setdefault(address, [])
            if len(idle) < IDLE_PER_ADDRESS:
                idle.append((time.monotonic(), connection))
                return
        connection.close()

    def clear(self) -> None:
        """Close every idle connection."""
        with self._lock:
            idle, self._idle = self._idle, {}
        for entries in idle.values():
            for _, connection in entries:
                connection.close()


def _idle_and_open(connection: Reusable) -> bool:
    """Report whether an idle connection has nothing to read.

    A readable idle HTTP/1.1 connection means that the peer closed it or sent bytes no request asked for.
    """
    sock = connection.sock
    if sock is None or (isinstance(sock, ssl.SSLSocket) and sock.pending()):
        return False
    with selectors.DefaultSelector() as selector:
        selector.register(sock, selectors.EVENT_READ)
        return not selector.select(0)
