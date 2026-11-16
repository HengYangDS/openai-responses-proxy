"""The log names a client's close once, and only where a read or a write of a request meets it."""

from __future__ import annotations

import http.client
import select
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from typing import cast
from typing import override

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff as controller
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.runtime import loopback
from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import server
from tests.relay.fixtures import DirectResponse

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pytest_mock import MockerFixture

_HANDLER_SECONDS = 10.0
# Long enough for a controller that leaves at the status to have left, on any host.
_HELD_SECONDS = 0.3
_PATH = "/dmxapi/v1/responses"
_COMPLETED_JSON = b'{"id":"resp_done","status":"completed","output":[]}'
_DELTA = b'data: {"type":"response.output_text.delta","delta":"kept"}\n\n'
_COMPLETED = b'data: {"type":"response.completed"}\n\n'


class _ObservedHandler(server.Handler):
    """Report each pass over the connection and the moment at which a request has been parsed."""

    @override
    def handle_one_request(self) -> None:
        cast("_ObservedServer", self.server).passes += 1
        super().handle_one_request()

    @override
    def parse_request(self) -> bool:
        parsed = super().parse_request()
        cast("_ObservedServer", self.server).request_parsed.set()
        return parsed

    @override
    def end_headers(self) -> None:
        """Write the head of the answer, then wait where a test holds the body back."""
        super().end_headers()
        observed = cast("_ObservedServer", self.server)
        observed.head_written.set()
        if observed.body_released is not None:
            observed.body_released.wait(_HANDLER_SECONDS)


class _ObservedServer(server.ResilientProxyServer):
    """Report each accepted connection, each error that leaves its handler, and its end."""

    def __init__(self, bindings: server.Bindings) -> None:
        self.connections: list[socket.socket] = []
        self.errors: list[BaseException | None] = []
        self.connection_accepted = threading.Event()
        self.request_parsed = threading.Event()
        self.handler_ended = threading.Event()
        self.head_written = threading.Event()
        # Set by a test that holds each answer's body back until it releases it.
        self.body_released: threading.Event | None = None
        self.passes = 0
        super().__init__(("127.0.0.1", 0), _ObservedHandler, bindings)

    @override
    def get_request(self) -> tuple[socket.socket, object]:
        request, address = super().get_request()
        self.connections.append(request)
        self.connection_accepted.set()
        return request, address

    @override
    def handle_error(self, request: object, client_address: object) -> None:
        self.errors.append(sys.exception())
        super().handle_error(request, client_address)

    @override
    def process_request_thread(self, request: object, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.handler_ended.set()


class _HeldResponse(DirectResponse):
    """An upstream response that waits for the test before one of its reads."""

    def __init__(self, *reads: bytes, held: int, content_type: str) -> None:
        super().__init__(*reads, content_type=content_type)
        self._reads_before_hold = held
        self.reached = threading.Event()
        self.release = threading.Event()

    @override
    def read(self, amount: int = -1) -> bytes:
        if self._reads_before_hold == 0:
            self.reached.set()
            self.release.wait(_HANDLER_SECONDS)
        self._reads_before_hold -= 1
        return super().read(amount)


@pytest.fixture
def log_path(tmp_path: Path, mocker: MockerFixture) -> Path:
    path = tmp_path / "proxy.log"
    mocker.patch.object(operational_log, "LOG_PATH", str(path))
    mocker.patch.object(operational_log.sys.stderr, "write")
    return path


@pytest.fixture
def proxy(mocker: MockerFixture) -> Generator[_ObservedServer]:
    bindings = server.Bindings(
        control=control.Bindings(
            runtime_status=lambda: {"status": "serving"}, handoff_context=mocker.Mock()
        ),
        providers=provider_registry.Registry(
            {"dmxapi": provider_registry.Profile("dmxapi", "https://upstream.invalid")}
        ),
        server_version="proxy/test",
    )
    admission.reset_for_test()
    telemetry.reset_for_test()
    cooldown.reset_for_test()
    listener = _ObservedServer(bindings)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield listener
    finally:
        # A handler that a failed test left held would write its answer after the test, when
        # the log's path is the host's own again: release it and wait for its end first.
        if listener.body_released is not None:
            listener.body_released.set()
            if listener.connections:
                listener.handler_ended.wait(_HANDLER_SECONDS)
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=_HANDLER_SECONDS)


def _reset(client: socket.socket) -> None:
    """Close with a zero linger time, which sends a reset instead of an orderly end."""
    client.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_LINGER,
        struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0),
    )
    client.close()


def _answered_connection(port: int) -> http.client.HTTPConnection:
    """Return a kept-alive connection whose one request has its complete response."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=_HANDLER_SECONDS)
    connection.request("GET", "/healthz")
    response = connection.getresponse()
    assert response.status == 200
    assert response.read() == b'{"status":"serving"}'
    assert not response.will_close
    return connection


def _await_close(proxy: _ObservedServer) -> None:
    """Wait until the client's close or reset has reached the listener's end of the connection.

    The handler waits for its upstream then and the request has been read whole, so that end
    turns readable only when the close arrives.
    """
    [connection] = proxy.connections
    readable, _, _ = select.select([connection], [], [], _HANDLER_SECONDS)
    assert readable == [connection]


def _log_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _fields(line: str) -> dict[str, str]:
    return dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)


def test_a_reset_between_two_requests_writes_no_line(
    proxy: _ObservedServer, log_path: Path
) -> None:
    connection = _answered_connection(proxy.server_port)

    _reset(connection.sock)

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert _log_lines(log_path) == []
    assert proxy.errors == []
    # The handler ends the connection with the pass that met the reset: a further pass would
    # read a dead connection, which can raise again and again.
    assert proxy.passes == 2


def test_a_handoff_request_that_is_answered_and_then_reset_writes_no_line(
    proxy: _ObservedServer, log_path: Path, *, mocker: MockerFixture
) -> None:
    """The controller closes the connection of its handoff request with a reset, always.

    It takes the whole answer first, as the product's controller does. The request asks for
    the connection to be closed, so the handler ends it after its one answer, and the reset
    that follows meets no read and no write of a request.
    """
    mocker.patch.object(control.handoff, "disk_payload_matches_expected", return_value=False)
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/control/handoff",
        data=b'{"transaction_id":"txn-1"}',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    connection = loopback.connect_reset_on_close(request, timeout_seconds=_HANDLER_SECONDS)

    with pytest.raises(urllib.error.HTTPError) as refused:
        loopback.open_request_on(connection, request)
    assert refused.value.code == 409
    # The whole answer: the listener has written its last byte before the reset can reach it.
    assert len(refused.value.read()) == int(refused.value.headers["Content-Length"])
    # Leaving the answer closes the connection, and that close is the reset.
    refused.value.close()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert _log_lines(log_path) == []
    assert proxy.errors == []
    assert proxy.passes == 1


def test_a_refusal_that_its_controller_leaves_before_the_body_is_named_once(
    proxy: _ObservedServer, log_path: Path, *, mocker: MockerFixture
) -> None:
    """Why the controller takes a refusal whole: a reset between the status and the body.

    The listener writes the head of its refusal and is held before the body. A controller that
    leaves at the status resets the connection, and the body's write meets that reset: one
    line names a request that its client closed. On Linux this order came by itself in about
    one refusal of five while the controller left at the status.
    """
    mocker.patch.object(control.handoff, "disk_payload_matches_expected", return_value=False)
    proxy.body_released = threading.Event()
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/control/handoff",
        data=b'{"transaction_id":"txn-1"}',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    connection = loopback.connect_reset_on_close(request, timeout_seconds=_HANDLER_SECONDS)

    with pytest.raises(urllib.error.HTTPError) as refused:
        loopback.open_request_on(connection, request)
    assert refused.value.code == 409
    refused.value.close()
    _await_close(proxy)
    proxy.body_released.set()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    [line] = _log_lines(log_path)
    [error] = proxy.errors
    assert isinstance(error, downstream.CLIENT_CLOSED)
    assert line.split()[1:] == [
        "event=client_closed_mid_request",
        f"exception={type(error).__name__}",
    ]
    assert proxy.passes == 1


@pytest.mark.parametrize("writer", ["send_error", "write_json"])
def test_the_controller_of_the_product_takes_a_held_refusal_whole(
    writer: str, proxy: _ObservedServer, log_path: Path, *, mocker: MockerFixture
) -> None:
    """The product's own controller against the listener's two ways of writing a refusal.

    The listener writes the head of its refusal and is held before the body. The controller
    has the status then and reads on: it is still in its request when the body is released,
    and its leaving meets no write. A controller that left at the status would have ended
    while the body was held. Measured on Linux with the body unheld and a client that left at
    the status: a line in 10 of 27 refusals that ``_write_json`` wrote and in 1 of 27 that
    ``send_error`` wrote, and in none of 27 of either for this controller.
    """
    matches = writer == "write_json"
    mocker.patch.object(control.handoff, "disk_payload_matches_expected", return_value=matches)
    mocker.patch.object(
        control.handoff,
        "prepare",
        side_effect=control.handoff.HandoffConflictError("a handoff is already in progress"),
    )
    proxy.body_released = threading.Event()
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/control/handoff",
        data=b'{"transaction_id":"txn-1"}',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    ended: list[errors.InstallError] = []

    def ask() -> None:
        connection = controller._connect(request, timeout_seconds=_HANDLER_SECONDS)
        try:
            controller._handoff_reply(connection, request)
        except errors.InstallError as failure:
            ended.append(failure)

    asking = threading.Thread(target=ask, daemon=True)
    asking.start()
    assert proxy.head_written.wait(_HANDLER_SECONDS)
    asking.join(_HELD_SECONDS)
    assert asking.is_alive()
    proxy.body_released.set()
    asking.join(_HANDLER_SECONDS)
    assert not asking.is_alive()

    [failure] = ended
    assert isinstance(failure, controller.HandoffRefusedError)
    assert str(failure) == "handoff control returned HTTP 409"
    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert _log_lines(log_path) == []
    assert proxy.errors == []
    assert proxy.passes == 1


def test_a_handoff_request_that_its_controller_gave_up_before_the_answer_is_named_once(
    proxy: _ObservedServer, log_path: Path, *, mocker: MockerFixture
) -> None:
    """The listener's answer to a request that was given up meets the controller's reset.

    The handler has read the whole request and decides while the controller's timeout ends.
    Its answer, here a refusal, is written to a connection that the controller has reset: one
    line names the request that its client closed, and the handler ends without a traceback.
    """
    given_up = threading.Event()

    def decide_after_the_reset(*_args: object) -> bool:
        assert given_up.wait(_HANDLER_SECONDS)
        _await_close(proxy)
        return False

    mocker.patch.object(
        control.handoff, "disk_payload_matches_expected", side_effect=decide_after_the_reset
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/control/handoff",
        data=b'{"transaction_id":"txn-1"}',
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    connection = loopback.connect_reset_on_close(request, timeout_seconds=0.2)

    with pytest.raises(TimeoutError):
        loopback.open_request_on(connection, request)
    given_up.set()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    [line] = _log_lines(log_path)
    [error] = proxy.errors
    assert isinstance(error, downstream.CLIENT_CLOSED)
    assert line.split()[1:] == [
        "event=client_closed_mid_request",
        f"exception={type(error).__name__}",
    ]
    assert proxy.passes == 1


def test_an_orderly_close_between_two_requests_writes_no_line(
    proxy: _ObservedServer, log_path: Path
) -> None:
    connection = _answered_connection(proxy.server_port)

    connection.close()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert _log_lines(log_path) == []
    assert proxy.errors == []


def test_a_reset_before_a_request_body_is_complete_is_a_request_the_client_closed(
    proxy: _ObservedServer, log_path: Path
) -> None:
    client = socket.create_connection(("127.0.0.1", proxy.server_port), timeout=_HANDLER_SECONDS)
    client.sendall(
        b"POST /dmxapi/v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: 64\r\n\r\n"
        b'{"input":'
    )
    assert proxy.request_parsed.wait(_HANDLER_SECONDS)

    _reset(client)

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    [line] = _log_lines(log_path)
    [error] = proxy.errors
    assert isinstance(error, downstream.CLIENT_CLOSED)
    assert line.split()[1:] == [
        "event=client_closed_mid_request",
        f"exception={type(error).__name__}",
    ]


def test_a_reset_after_part_of_a_request_line_writes_no_line(
    proxy: _ObservedServer, log_path: Path
) -> None:
    client = socket.create_connection(("127.0.0.1", proxy.server_port), timeout=_HANDLER_SECONDS)
    client.sendall(b"POST /dmxapi/v1/resp")
    assert proxy.connection_accepted.wait(_HANDLER_SECONDS)

    _reset(client)

    # No request line was read: a line ends with its newline.
    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert _log_lines(log_path) == []
    assert proxy.errors == []


_ADMITTED = pytest.mark.parametrize(
    ("reads", "held", "content_type", "sent", "ending"),
    [
        ((_COMPLETED_JSON,), 0, "application/json", b"", "json_terminal"),
        ((_DELTA, _COMPLETED), 1, "text/event-stream", _DELTA, "sse_terminal"),
    ],
    ids=["a response that is not a stream", "a stream after its first event"],
)


def _held_response(
    proxy: _ObservedServer, upstream: _HeldResponse, sent: bytes, mocker: MockerFixture
) -> tuple[socket.socket, bytes]:
    """Send one request and return the client with what it has of the answer so far.

    The upstream is held then, so the handler writes nothing more until the test releases it.
    """
    mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=upstream)
    body = b'{"input":"current"}'
    client = socket.create_connection(("127.0.0.1", proxy.server_port), timeout=_HANDLER_SECONDS)
    client.sendall(
        f"POST {_PATH} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    assert upstream.reached.wait(_HANDLER_SECONDS)
    received = b""
    while sent not in received:
        chunk = client.recv(65536)
        assert chunk
        received += chunk
    return client, received


def _admitted_events(log_path: Path) -> tuple[list[str], list[str]]:
    """Return the lines from the admission on, and their events."""
    lines = _log_lines(log_path)
    events = [_fields(line)["event"] for line in lines]
    admitted = events.index("responses_admitted")
    return lines[admitted:], events[admitted:]


@_ADMITTED
def test_a_reset_that_a_write_of_an_admitted_response_meets_is_named_once(
    proxy: _ObservedServer,
    log_path: Path,
    *,
    reads: tuple[bytes, ...],
    held: int,
    content_type: str,
    sent: bytes,
    ending: str,
    mocker: MockerFixture,
) -> None:
    del ending
    upstream = _HeldResponse(*reads, held=held, content_type=content_type)
    client, _received = _held_response(proxy, upstream, sent, mocker)

    _reset(client)
    _await_close(proxy)
    upstream.release.set()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    lines, events = _admitted_events(log_path)
    # The close is the last line before the release, and nothing follows the release.
    assert events == ["responses_admitted", "downstream_client_closed", "responses_released"]
    assert _fields(lines[1]) == {
        "req": "1",
        "event": "downstream_client_closed",
        "provider": "dmxapi",
        "path": _PATH,
    }
    # The handler ends the connection: no error leaves it, and it makes no further pass that
    # would meet the close again.
    assert proxy.errors == []
    assert proxy.passes == 1


@_ADMITTED
def test_an_orderly_close_that_no_write_meets_leaves_the_responses_own_ending(
    proxy: _ObservedServer,
    log_path: Path,
    *,
    reads: tuple[bytes, ...],
    held: int,
    content_type: str,
    sent: bytes,
    ending: str,
    mocker: MockerFixture,
) -> None:
    upstream = _HeldResponse(*reads, held=held, content_type=content_type)
    client, received = _held_response(proxy, upstream, sent, mocker)

    # The client ends its sending side and keeps reading, so no write to it can fail.
    client.shutdown(socket.SHUT_WR)
    _await_close(proxy)
    upstream.release.set()
    while chunk := client.recv(65536):
        received += chunk
    client.close()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    assert received.startswith(b"HTTP/1.1 200 ")
    assert reads[-1] in received
    _lines, events = _admitted_events(log_path)
    assert events == ["responses_admitted", ending, "responses_released"]
    assert proxy.errors == []
