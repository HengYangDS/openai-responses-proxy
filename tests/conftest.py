"""Test configuration that every tree shares: a test reaches only a listener that it bound.

A connection from the test process to a port that this process never bound would read whatever
listens there, on the host or on the network, an installed service among them. Every such
connection of an internet socket is refused, whatever host its address names. A port that this
process bound stays admitted after its socket is closed, because a test hands such a port to a
child process that then listens there.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

_BIND = socket.socket.bind
_CONNECT = socket.socket.connect
_CONNECT_EX = socket.socket.connect_ex
_INTERNET = frozenset({socket.AF_INET, socket.AF_INET6})
type _Address = tuple[object, ...] | str | bytes


class ForeignListenerError(ConnectionRefusedError):
    """A test tried to reach a port that the test process did not bind."""


class OwnListeners:
    """The ports that this process bound, and the connections refused for want of one."""

    def __init__(self) -> None:
        """Start with no port bound and no connection refused."""
        self.bound: set[int] = set()
        self.refused: list[tuple[object, object]] = []

    def admit(self, family: int, address: object) -> None:
        """Refuse an internet address unless this process bound its port."""
        if family not in _INTERNET:
            return
        pair = isinstance(address, tuple) and len(address) > 1
        host, port = (address[0], address[1]) if pair else (address, None)
        if isinstance(port, int) and port in self.bound:
            return
        self.refused.append((host, port))
        raise ForeignListenerError(_refusal([(host, port)]))


def _refusal(targets: list[tuple[object, object]]) -> str:
    named = ", ".join(f"{host!r} port {port}" for host, port in targets)
    return (
        f"connection to {named} refused: the test process did not bind that port, "
        "and a test binds its own listener"
    )


_GUARD = OwnListeners()


def _bind(self: socket.socket, address: _Address, /) -> None:
    _BIND(self, address)
    if self.family in _INTERNET:
        _GUARD.bound.add(self.getsockname()[1])


def _connect(self: socket.socket, address: _Address, /) -> None:
    _GUARD.admit(self.family, address)
    _CONNECT(self, address)


def _connect_ex(self: socket.socket, address: _Address, /) -> int:
    _GUARD.admit(self.family, address)
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
    """Fail a test whose connection was refused, also when the test swallowed the error.

    A refusal that a fixture of a wider scope met before the test counts against that test, and
    one met after a test's teardown, by a thread that outlives it or by a wider fixture's own
    teardown, counts against the next test.
    """
    try:
        yield _GUARD
        assert not _GUARD.refused, _refusal(_GUARD.refused)
    finally:
        _GUARD.refused.clear()
