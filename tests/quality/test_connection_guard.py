"""A test connects only to a loopback listener that the test process bound."""

from __future__ import annotations

import socket
import subprocess
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Protocol

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# A listener in another process. It lives as long as its standard input stays open, so it ends
# with the test that started it.
_LISTENER = """
import socket
import sys
import threading

listener = socket.create_server(("127.0.0.1", int(sys.argv[1])))


def serve():
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.sendall(b"served by the child")


threading.Thread(target=serve, daemon=True).start()
print(listener.getsockname()[1], flush=True)
sys.stdin.read()
"""
_SECONDS = 10.0
_REFUSAL = r"loopback port {port} .*did not bind.*a test binds its own listener"


class _OwnListeners(Protocol):
    """The guard that the shared test configuration hands to a test."""

    refused: list[int]

    def admit(self, address: object) -> None:
        """Refuse a loopback address whose port the test process did not bind."""

    def require_none_refused(self) -> None:
        """Fail when a connection was refused."""


@contextmanager
def _child_listener(port: int = 0) -> Generator[int]:
    """Yield the port on which a child process listens: ``port``, or one that the child chose."""
    with subprocess.Popen(
        [sys.executable, "-c", _LISTENER, str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as child:
        assert child.stdout is not None
        yield int(child.stdout.readline())


def _read(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=_SECONDS) as client:
        return client.recv(64)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.7.6", "localhost", "LocalHost", "::1"])
def test_a_connection_to_a_listener_that_this_process_did_not_bind_is_refused(
    host: str, own_listeners: _OwnListeners
) -> None:
    with _child_listener() as port, socket.socket() as client:
        with pytest.raises(ConnectionRefusedError, match=_REFUSAL.format(port=port)):
            client.connect((host, port))
        with pytest.raises(ConnectionRefusedError, match=_REFUSAL.format(port=port)):
            client.connect_ex((host, port))

    # A test that swallows the refusal still fails when it ends.
    assert own_listeners.refused == [port, port]
    with pytest.raises(AssertionError, match=_REFUSAL.format(port=port)):
        own_listeners.require_none_refused()
    own_listeners.refused.clear()
    own_listeners.require_none_refused()


def test_a_connection_to_a_listener_that_this_process_bound_is_admitted(
    own_listeners: _OwnListeners,
) -> None:
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=_SECONDS):
            connection, _ = listener.accept()
            connection.close()

    assert own_listeners.refused == []


def test_a_port_that_this_process_bound_stays_admitted_when_a_child_listens_there(
    own_listeners: _OwnListeners,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with _child_listener(port) as served:
        assert served == port
        assert _read(port) == b"served by the child"

    assert own_listeners.refused == []


@pytest.mark.parametrize(
    "address",
    [("192.0.2.1", 9), ("upstream.invalid", 443), "/run/service.sock", ("127.0.0.1",)],
    ids=["another host", "a name", "a path", "no port"],
)
def test_an_address_that_is_no_loopback_port_is_not_the_guards_to_refuse(
    address: object, own_listeners: _OwnListeners
) -> None:
    own_listeners.admit(address)

    assert own_listeners.refused == []
