"""The connection of a request that its sender can give up: every close of it is a reset.

A double that records its calls holds the order of the steps, and real loopback sockets hold
what the other end of such a connection sees on each platform. No test here starts a thread:
the kernel completes a loopback connection and keeps what is written on it before the
listening socket accepts.
"""

from __future__ import annotations

import errno
import http.client
import io
import socket
import struct
import subprocess
import sys
import urllib.request
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.runtime import loopback

if TYPE_CHECKING:
    from collections.abc import Generator

    from pytest_mock import MockerFixture

_BODY = b'{"transaction_id":"txn-1"}'
_ANSWER = b'{"ok":true}'
_SECONDS = 5.0


def _request(port: int) -> urllib.request.Request:
    return urllib.request.Request(
        f"http://127.0.0.1:{port}/control/handoff",
        data=_BODY,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )


class _RecordedSocket:
    """A connected socket that records each option, each write and each close, in order."""

    def __init__(
        self,
        calls: list[tuple[object, ...]],
        *,
        refuse_linger: bool = False,
        refuse_no_delay: bool = False,
    ) -> None:
        self.calls = calls
        self._refuse_linger = refuse_linger
        self._refuse_no_delay = refuse_no_delay

    def setsockopt(self, level: int, option: int, value: object) -> None:
        self.calls.append(("option", level, option, value))
        if self._refuse_linger and (level, option) == (socket.SOL_SOCKET, socket.SO_LINGER):
            raise OSError("the option is not supported")
        if self._refuse_no_delay and (level, option) == (socket.IPPROTO_TCP, socket.TCP_NODELAY):
            # What macOS answers on a connection that the listener's full queue has reset.
            raise OSError(errno.EINVAL, "Invalid argument")

    def sendall(self, data: bytes) -> None:
        self.calls.append(("write", bytes(data)))

    def makefile(self, _mode: str, *_args: object, **_options: object) -> io.BytesIO:
        answer = b"HTTP/1.1 202 Accepted\r\nContent-Length: %d\r\n\r\n" % len(_ANSWER)
        return io.BytesIO(answer + _ANSWER)

    def close(self) -> None:
        self.calls.append(("close",))


def _linger(calls: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    return [call for call in calls if call[:3] == ("option", socket.SOL_SOCKET, socket.SO_LINGER)]


def _written(calls: list[tuple[object, ...]]) -> bytes:
    return b"".join(call[1] for call in calls if call[0] == "write" and isinstance(call[1], bytes))


def _unpacked(value: object) -> tuple[int, int]:
    """Read the option as the platform's own structure: whether it lingers, and for how long."""
    assert isinstance(value, bytes)
    on, seconds = struct.unpack("HH" if sys.platform == "win32" else "ii", value)
    assert isinstance(on, int)
    assert isinstance(seconds, int)
    return on, seconds


class TestTheOrderOfTheSteps:
    def test_the_option_is_set_after_the_connect_and_before_the_first_byte(
        self, *, mocker: MockerFixture
    ) -> None:
        """Connect, then the option with on and zero, then the request, then the close."""
        calls: list[tuple[object, ...]] = []

        def connect(address: object, *_args: object, **_options: object) -> _RecordedSocket:
            calls.append(("connect", address))
            return _RecordedSocket(calls)

        mocker.patch.object(socket, "create_connection", side_effect=connect)
        request = _request(8791)

        connection = loopback.connect_reset_on_close(request, timeout_seconds=_SECONDS)

        # Nothing of the request is written by the step that opens the connection.
        assert calls[0] == ("connect", ("127.0.0.1", 8791))
        assert [call[0] for call in calls].count("write") == 0
        [option] = _linger(calls)
        assert _unpacked(option[3]) == (1, 0)
        assert option[3] == loopback.reset_on_close()

        with loopback.open_request_on(connection, request) as response:
            assert response.status == 202
            assert response.read() == _ANSWER

        kinds = [call[0] for call in calls]
        first_write = kinds.index("write")
        assert kinds.index("connect") < calls.index(option) < first_write
        written = _written(calls)
        assert written.startswith(b"POST /control/handoff HTTP/1.1\r\n")
        assert written.endswith(b"\r\n\r\n" + _BODY)
        # The option is set once and never again, and the connection is closed at the end.
        assert len(_linger(calls)) == 1
        assert kinds[-1] == "close"

    def test_a_connect_that_fails_writes_nothing(self, *, mocker: MockerFixture) -> None:
        for failure in (
            ConnectionRefusedError("refused"),
            ConnectionResetError("reset"),
            TimeoutError("timed out"),
        ):
            mocker.patch.object(socket, "create_connection", side_effect=failure)
            with pytest.raises(type(failure)) as raised:
                loopback.connect_reset_on_close(_request(8791), timeout_seconds=_SECONDS)
            assert raised.value is failure

    def test_an_option_that_cannot_be_set_sends_nothing_and_closes(
        self, *, mocker: MockerFixture
    ) -> None:
        """A request that cannot be withdrawn is not sent."""
        calls: list[tuple[object, ...]] = []
        mocker.patch.object(
            socket, "create_connection", return_value=_RecordedSocket(calls, refuse_linger=True)
        )

        with pytest.raises(OSError, match="the option is not supported"):
            loopback.connect_reset_on_close(_request(8791), timeout_seconds=_SECONDS)

        kinds = [call[0] for call in calls]
        assert "write" not in kinds
        assert kinds[-1] == "close"

    def test_a_connect_that_fails_after_its_socket_exists_closes_it_unwritten(
        self, *, mocker: MockerFixture
    ) -> None:
        """The connect sets an option of its own first; a reset connection refuses it."""
        calls: list[tuple[object, ...]] = []
        mocker.patch.object(
            socket, "create_connection", return_value=_RecordedSocket(calls, refuse_no_delay=True)
        )

        with pytest.raises(OSError, match="Invalid argument"):
            loopback.connect_reset_on_close(_request(8791), timeout_seconds=_SECONDS)

        kinds = [call[0] for call in calls]
        assert "write" not in kinds
        assert _linger(calls) == []
        assert kinds[-1] == "close"

    def test_a_closed_connection_is_never_opened_again(self, *, mocker: MockerFixture) -> None:
        """A second connection would carry no option, so a second request fails unsent."""
        calls: list[tuple[object, ...]] = []
        opened = mocker.patch.object(
            socket, "create_connection", return_value=_RecordedSocket(calls)
        )
        request = _request(8791)
        connection = loopback.connect_reset_on_close(request, timeout_seconds=_SECONDS)
        with loopback.open_request_on(connection, request) as response:
            response.read()
        written = _written(calls)

        with pytest.raises(http.client.NotConnected):
            loopback.open_request_on(connection, request)

        opened.assert_called_once()
        assert _written(calls) == written


# A sender that ends as a killed process does: no close, no flush, no exit handler. It loads
# the transport by its file, which needs the standard library alone.
_END_WITHOUT_CLOSING = """
import importlib.util, os, sys, urllib.request
spec = importlib.util.spec_from_file_location("loopback", sys.argv[1])
loopback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loopback)
request = urllib.request.Request(
    f"http://127.0.0.1:{sys.argv[2]}/control/handoff", data=b"{}", method="POST"
)
connection = loopback.connect_reset_on_close(request, timeout_seconds=5.0)
connection.sock.sendall(b"POST /control/handoff HTTP/1.1\\r\\n")
os._exit(0)
"""


@pytest.fixture
def listening() -> Generator[socket.socket]:
    """A private loopback listening socket that only the test accepts from."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        sock.settimeout(_SECONDS)
        yield sock
    finally:
        sock.close()


def _accept(listening: socket.socket) -> socket.socket | None:
    """Return the next connection of the queue, or nothing where the platform gives none."""
    try:
        accepted, _peer = listening.accept()
    except (ConnectionError, TimeoutError):
        return None
    return accepted


def _read_to_the_end(accepted: socket.socket) -> tuple[bytes, str]:
    """Return what the listener's end reads, and how the reading ends."""
    accepted.settimeout(_SECONDS)
    received = b""
    try:
        while chunk := accepted.recv(65536):
            received += chunk
    except ConnectionResetError:
        return received, "reset"
    return received, "end of stream"


class TestWhatTheOtherEndSees:
    def test_a_request_that_is_given_up_at_its_timeout_ends_with_a_reset(
        self, listening: socket.socket
    ) -> None:
        """The listener that accepts afterwards reads a reset, never an ordinary end of stream.

        The test accepts nothing while the request is sent and runs into its timeout. What a
        platform delivers before the reset:

        - Linux 7.2 (measured 2026-11-16): the whole request, then the reset.
        - macOS 27.2 (measured 2026-11-16): the whole request, then the reset.
        - Windows: not measured when this was written. It may deliver the request or any
          part of it first, and it may give the listener no connection at all, by an accept
          that fails or that finds nothing: such a listener never receives the request.
        """
        request = _request(listening.getsockname()[1])
        connection = loopback.connect_reset_on_close(request, timeout_seconds=0.2)

        with pytest.raises(TimeoutError):
            loopback.open_request_on(connection, request)

        accepted = _accept(listening)
        if accepted is None:
            # Only where the platform takes a reset connection out of the queue again.
            assert sys.platform == "win32"
            return
        with accepted:
            received, ending = _read_to_the_end(accepted)
        assert ending == "reset"
        assert received == b"" or received.startswith(b"POST /control/handoff HTTP/1.1\r\n")
        assert received == b"" or _BODY.startswith(received.partition(b"\r\n\r\n")[2])

    def test_a_request_that_was_answered_ends_with_a_reset_as_well(
        self, listening: socket.socket
    ) -> None:
        """After a complete answer the sender's close is a reset too, on every platform.

        The listener's end is accepted and its answer written before the request is sent: the
        kernel keeps the answer until the sender reads it, so no thread is needed. The
        listener's end then reads the whole request, and after it the reset.
        """
        request = _request(listening.getsockname()[1])
        connection = loopback.connect_reset_on_close(request, timeout_seconds=_SECONDS)
        accepted, _peer = listening.accept()
        with accepted:
            accepted.sendall(
                b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n"
                b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(_ANSWER) + _ANSWER
            )

            with loopback.open_request_on(connection, request) as response:
                assert response.status == 202
                assert response.read() == _ANSWER

            received, ending = _read_to_the_end(accepted)
        assert ending == "reset"
        # A reset may take what the listener's end had not read yet; it adds nothing.
        assert received == b"" or received.endswith(b"\r\n\r\n" + _BODY)

    def test_a_sender_whose_process_ends_leaves_a_reset_as_well(
        self, listening: socket.socket
    ) -> None:
        """The kernel closes the connection of a process that ends, and that close is a reset.

        A child process opens the connection as the controller does, writes the beginning of
        a request and ends without closing anything, as a killed controller ends. On Windows
        the listener may get no connection at all, as after a request that was given up.
        """
        ended = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _END_WITHOUT_CLOSING,
                loopback.__file__,
                str(listening.getsockname()[1]),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert (ended.returncode, ended.stderr) == (0, b"")

        accepted = _accept(listening)
        if accepted is None:
            assert sys.platform == "win32"
            return
        with accepted:
            received, ending = _read_to_the_end(accepted)
        assert ending == "reset"
        assert b"POST /control/handoff HTTP/1.1\r\n".startswith(received)
