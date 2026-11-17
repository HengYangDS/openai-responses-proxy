"""Test configuration that every tree shares: a test reaches only its own listener and log.

A connection from the test process to a port that this process never bound would read whatever
listens there, on the host or on the network, an installed service among them. Every such
connection of an internet socket is refused, whatever host its address names. A port that this
process bound stays admitted after its socket is closed, because a test hands such a port to a
child process that then listens there.

The product's default logs are those of the host's operator, and a thread that outlives its
test logs to the default. Each session therefore gives the product a state directory of its
own before a product module computes a path, and a log line to a path outside the temporary
root of the run is refused before any file operation.

The same directory holds the session's temporary base. This repository keeps no temporary
directory after its test, and under that policy pytest does not lock a session's numbered base
in the user's shared root: a session that ends removes the unlocked bases that it finds there,
a running session's among them.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.runtime import bounded_log
from openai_responses_proxy.runtime import config as runtime_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Generator

_BIND = socket.socket.bind
_CONNECT = socket.socket.connect
_CONNECT_EX = socket.socket.connect_ex
_APPEND = bounded_log.append
_INTERNET = frozenset({socket.AF_INET, socket.AF_INET6})
type _Address = tuple[object, ...] | str | bytes


class ForeignListenerError(ConnectionRefusedError):
    """A test tried to reach a port that the test process did not bind."""


class ForeignLogError(PermissionError):
    """A test tried to write a log outside the temporary root of its run."""


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


class OwnLogs:
    """The directories below which this run logs, and the log paths refused outside them."""

    def __init__(self) -> None:
        """Start with no directory admitted and no path refused, until the session is bound."""
        self.session = Path()
        self.roots: tuple[Path, ...] = ()
        self.refused: list[Path] = []

    def admit(self, path: Path) -> None:
        """Refuse a path that, resolved, lies below none of the run's temporary roots."""
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in self.roots):
            return
        self.refused.append(path)
        raise ForeignLogError(_log_refusal([path]))


def _refusal(targets: list[tuple[object, object]]) -> str:
    named = ", ".join(f"{host!r} port {port}" for host, port in targets)
    return (
        f"connection to {named} refused: the test process did not bind that port, "
        "and a test binds its own listener"
    )


def _log_refusal(paths: list[Path]) -> str:
    named = ", ".join(str(path) for path in paths)
    return (
        f"log line to {named} refused: the path lies outside the temporary root of this run, "
        "and a test keeps its log below that root"
    )


_GUARD = OwnListeners()
_LOGS = OwnLogs()


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


def _append(
    path: Path,
    message: str,
    *,
    max_bytes: int,
    backup_count: int,
    mirror: Callable[[str], object] | None = None,
) -> None:
    _LOGS.admit(path)
    _APPEND(path, message, max_bytes=max_bytes, backup_count=backup_count, mirror=mirror)


def _bind_session(config: pytest.Config, patch: pytest.MonkeyPatch) -> None:
    """Give the session its own state directory and temporary base, and guard its logs.

    The environment carries the binding, not a module attribute, because a module that is loaded
    again computes its paths again. The two names of a log file would override the directory.
    """
    session = Path(tempfile.mkdtemp(prefix="proxy-test-session-")).resolve()
    config.add_cleanup(partial(shutil.rmtree, session, ignore_errors=True))
    patch.setenv(runtime_config.STATE_HOME_ENV, str(session / "state"))
    patch.delenv(runtime_config.PROXY_LOG_ENV, raising=False)
    patch.delenv(runtime_config.WATCHDOG_LOG_ENV, raising=False)
    if config.getoption("basetemp", None) is None:
        # A base that the session names is not one of the shared root's numbered directories,
        # so no other session's end can remove it; it leaves with the session's directory.
        patch.setattr(config.option, "basetemp", str(session / "base"))
    basetemp = config.getoption("basetemp", None)
    roots = [tempfile.gettempdir(), *([] if basetemp is None else [basetemp])]
    patch.setattr(_LOGS, "session", session)
    patch.setattr(_LOGS, "roots", tuple(Path(root).resolve() for root in roots))
    patch.setattr(bounded_log, "append", _append)


def pytest_configure(config: pytest.Config) -> None:
    """Guard every socket and log of this process, which is each worker of a distributed run."""
    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "bind", _bind)
    patch.setattr(socket.socket, "connect", _connect)
    patch.setattr(socket.socket, "connect_ex", _connect_ex)
    config.add_cleanup(patch.undo)
    _bind_session(config, patch)


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


@pytest.fixture(autouse=True)
def own_logs() -> Generator[OwnLogs]:
    """Fail a test whose log line was refused, also when the product swallowed the error.

    A refusal counts against a test as a refused connection does: before the test it counts
    against that test, and after its teardown against the next one.
    """
    try:
        yield _LOGS
        assert not _LOGS.refused, _log_refusal(_LOGS.refused)
    finally:
        _LOGS.refused.clear()
