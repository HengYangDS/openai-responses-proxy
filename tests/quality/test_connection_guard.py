"""A test connects only to a port that the test process bound."""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
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
# A test file that a child pytest runs under a copy of the shared configuration.
_SWALLOWS = """
import contextlib
import socket


def test_swallows_the_refusal():
    with contextlib.suppress(OSError), socket.socket() as client:
        client.connect(("127.0.0.1", 9))


def test_binds_its_own_listener():
    with socket.create_server(("127.0.0.1", 0)) as listener:
        socket.create_connection(listener.getsockname()[:2]).close()
"""
_SECONDS = 10.0
_CHILD_SECONDS = 60.0
# The address that names every interface: a client that connects to it reaches this host.
_EVERY_INTERFACE = str(ipaddress.IPv4Address(0))


class _OwnListeners(Protocol):
    """The guard that the shared test configuration hands to a test."""

    refused: list[tuple[object, object]]

    def admit(self, family: int, address: object) -> None:
        """Refuse an internet address whose port the test process did not bind."""


def _refusal(host: object, port: int) -> str:
    return re.escape(
        f"connection to {host!r} port {port} refused: the test process did not bind that port, "
        "and a test binds its own listener"
    )


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


@pytest.fixture(scope="module")
def foreign_port() -> Generator[int]:
    """Yield a loopback port on which another process listens."""
    with _child_listener() as port:
        yield port


def _read(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=_SECONDS) as client:
        return client.recv(64)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.8.7.6",
        "localhost",
        "LocalHost",
        "::1",
        _EVERY_INTERFACE,
        "",
        b"127.0.0.1",
        "127.1",
        "2130706433",
        "localhost.",
        "192.0.2.1",
    ],
)
def test_a_connection_to_a_port_that_this_process_did_not_bind_is_refused_whatever_its_host(
    host: str | bytes, foreign_port: int, own_listeners: _OwnListeners
) -> None:
    with socket.socket() as client:
        client.settimeout(_SECONDS)
        with pytest.raises(ConnectionRefusedError, match=_refusal(host, foreign_port)):
            client.connect((host, foreign_port))
        with pytest.raises(ConnectionRefusedError, match=_refusal(host, foreign_port)):
            client.connect_ex((host, foreign_port))

    assert own_listeners.refused == [(host, foreign_port)] * 2
    # This test met its refusals on purpose; left in place they would fail it when it ends.
    own_listeners.refused.clear()


@pytest.mark.parametrize(
    "address", [("127.0.0.1",), ("127.0.0.1", "http"), "127.0.0.1:80"], ids=repr
)
def test_an_internet_address_without_a_port_that_was_bound_is_refused(
    address: object, own_listeners: _OwnListeners
) -> None:
    with pytest.raises(ConnectionRefusedError, match="a test binds its own listener"):
        own_listeners.admit(socket.AF_INET6, address)

    assert len(own_listeners.refused) == 1
    own_listeners.refused.clear()


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


def test_a_socket_of_another_family_is_not_the_guards_to_refuse(
    own_listeners: _OwnListeners,
) -> None:
    own_listeners.admit(socket.AF_UNSPEC, "/run/service.sock")

    assert own_listeners.refused == []


def test_a_test_that_swallows_the_refusal_fails_when_it_ends(tmp_path: Path) -> None:
    shared = Path(__file__).resolve().parents[1] / "conftest.py"
    (tmp_path / "conftest.py").write_bytes(shared.read_bytes())
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_swallows.py").write_text(_SWALLOWS, encoding="utf-8")

    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", "."],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_CHILD_SECONDS,
        check=False,
    )

    # Both tests pass their own bodies; the fixture fails the one whose refusal it found.
    assert child.returncode == 1, child.stdout + child.stderr
    assert "2 passed, 1 error" in child.stdout
    assert "ERROR at teardown of test_swallows_the_refusal" in child.stdout
    assert re.search(_refusal("127.0.0.1", 9), child.stdout)
