"""Test configuration that every tree shares: a test reaches only a listener that it bound.

A connection from the test process to a loopback port that this process never bound would read
whatever listens there on the host, an installed service among them. Such a connection is
refused. A port that this process bound stays admitted after its socket is closed, because a
test hands such a port to a child process that then listens there.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

_BIND = socket.socket.bind
_CONNECT = socket.socket.connect
_CONNECT_EX = socket.socket.connect_ex
type _Address = tuple[object, ...] | str | bytes


class ForeignListenerError(ConnectionRefusedError):
    """A test tried to reach a loopback port that the test process did not bind."""


def _loopback_port(address: object) -> int | None:
    """Return the port of an address on 127.0.0.0/8, ``::1`` or ``localhost``, or nothing."""
    if not isinstance(address, tuple) or len(address) < 2:
        return None
    host, port = address[0], address[1]
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    if host.casefold() == "localhost":
        return port
    try:
        return port if ipaddress.ip_address(host).is_loopback else None
    except ValueError:
        return None


class OwnListeners:
    """The loopback ports that this process bound, and the connections refused for want of one."""

    def __init__(self) -> None:
        """Start with no port bound and no connection refused."""
        self.bound: set[int] = set()
        self.refused: list[int] = []

    def admit(self, address: object) -> None:
        """Refuse a loopback address whose port this process did not bind."""
        port = _loopback_port(address)
        if port is None or port in self.bound:
            return
        self.refused.append(port)
        raise ForeignListenerError(_refusal([port]))

    def require_none_refused(self) -> None:
        """Fail a test that had a connection refused, also when the test swallowed the error."""
        assert not self.refused, _refusal(self.refused)


def _refusal(ports: list[int]) -> str:
    named = ", ".join(str(port) for port in sorted(set(ports)))
    return (
        f"connection to loopback port {named} refused: the test process did not bind it, "
        "and a test binds its own listener"
    )


_GUARD = OwnListeners()


def _bind(self: socket.socket, address: _Address, /) -> None:
    _BIND(self, address)
    if self.family in {socket.AF_INET, socket.AF_INET6}:
        _GUARD.bound.add(self.getsockname()[1])


def _connect(self: socket.socket, address: _Address, /) -> None:
    _GUARD.admit(address)
    _CONNECT(self, address)


def _connect_ex(self: socket.socket, address: _Address, /) -> int:
    _GUARD.admit(address)
    return _CONNECT_EX(self, address)


def pytest_configure(config: pytest.Config) -> None:
    """Guard every socket of this process, which is each worker of a distributed run."""
    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "bind", _bind)
    patch.setattr(socket.socket, "connect", _connect)
    patch.setattr(socket.socket, "connect_ex", _connect_ex)
    config.add_cleanup(patch.undo)


@pytest.fixture(autouse=True)
def own_listeners() -> Generator[OwnListeners]:
    """Fail a test whose connection to a loopback port was refused.

    A refusal that a fixture of a wider scope met before the test counts against that test.
    """
    try:
        yield _GUARD
        _GUARD.require_none_refused()
    finally:
        _GUARD.refused.clear()
