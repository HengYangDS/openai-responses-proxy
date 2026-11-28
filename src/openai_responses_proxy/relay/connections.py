"""Keep-alive reuse of upstream connections behind urllib's own request pipeline.

urllib's ``AbstractHTTPHandler.do_open`` builds each request's headers, wraps send errors and leaves
non-2xx statuses to ``HTTPError`` as before. It also forces ``Connection: close``, so every exchange
used to pay for a new TCP connection and TLS handshake. A connection returns to this pool only after
its response was read to the end without an announced close, and an idle one is reused only while it
is fresh and its peer has not closed it.

The pool is bounded by the listener, not by the clients: at most `IDLE_PER_ADDRESS` idle connections
per address and `IDLE_TOTAL` in all, and `Pool.expire`, which the listener's serve loop runs, closes
every connection idle for longer than `IDLE_SECONDS` and forgets addresses left without one.
"""

from __future__ import annotations

import http.client
import selectors
import ssl
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

IDLE_SECONDS = 30.0
IDLE_PER_ADDRESS = 8
IDLE_TOTAL = 32

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
                if not idle:
                    del self._idle[address]
            if time.monotonic() - since <= IDLE_SECONDS and _idle_and_open(connection):
                return connection
            connection.close()

    def release(self, address: Address, connection: C) -> None:
        """Keep a connection whose response completed, within the per-address and total bounds."""
        evicted: C | None = connection
        with self._lock:
            idle = self._idle.setdefault(address, [])
            if len(idle) < IDLE_PER_ADDRESS:
                idle.append((time.monotonic(), connection))
                evicted = self._evict_oldest_locked() if self._count_locked() > IDLE_TOTAL else None
            elif not idle:
                del self._idle[address]
        if evicted is not None:
            evicted.close()

    def expire(self) -> None:
        """Close every connection idle for longer than `IDLE_SECONDS` and forget empty addresses."""
        now = time.monotonic()
        expired: list[C] = []
        with self._lock:
            for address, idle in tuple(self._idle.items()):
                expired.extend(
                    connection for since, connection in idle if now - since > IDLE_SECONDS
                )
                kept = [
                    (since, connection) for since, connection in idle if now - since <= IDLE_SECONDS
                ]
                if kept:
                    self._idle[address] = kept
                else:
                    del self._idle[address]
        for connection in expired:
            connection.close()

    def clear(self) -> None:
        """Close every idle connection."""
        with self._lock:
            idle, self._idle = self._idle, {}
        for entries in idle.values():
            for _, connection in entries:
                connection.close()

    def idle_for_test(self) -> dict[Address, int]:
        """Expose only the number of idle connections per address for bounded-state tests."""
        with self._lock:
            return {address: len(idle) for address, idle in self._idle.items()}

    def _count_locked(self) -> int:
        return sum(len(idle) for idle in self._idle.values())

    def _evict_oldest_locked(self) -> C:
        """Remove and return the least recently released idle connection."""
        address, index = min(
            (
                (address, index)
                for address, idle in self._idle.items()
                for index in range(len(idle))
            ),
            key=lambda position: self._idle[position[0]][position[1]][0],
        )
        idle = self._idle[address]
        _, connection = idle.pop(index)
        if not idle:
            del self._idle[address]
        return connection


def _idle_and_open(connection: Reusable) -> bool:
    """Report whether an idle connection has nothing to read.

    A readable idle HTTP/1.1 connection means that the peer closed it or sent bytes no request asked
    for.
    """
    sock = connection.sock
    if sock is None or (isinstance(sock, ssl.SSLSocket) and sock.pending()):
        return False
    with selectors.DefaultSelector() as selector:
        selector.register(sock, selectors.EVENT_READ)
        return not selector.select(0)
