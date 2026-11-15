"""The log names a client's reset once, and only while one of its requests is read or answered."""

from __future__ import annotations

import http.client
import select
import socket
import struct
import sys
import threading
from typing import TYPE_CHECKING
from typing import cast
from typing import override

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import server
from tests.relay.fixtures import DirectResponse

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pytest_mock import MockerFixture

_HANDLER_SECONDS = 10.0
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


class _ObservedServer(server.ResilientProxyServer):
    """Report each accepted connection, each error that leaves its handler, and its end."""

    def __init__(self, bindings: server.Bindings) -> None:
        self.connections: list[socket.socket] = []
        self.errors: list[BaseException | None] = []
        self.connection_accepted = threading.Event()
        self.request_parsed = threading.Event()
        self.handler_ended = threading.Event()
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


def _await_reset(proxy: _ObservedServer) -> None:
    """Wait until the client's reset has reached the listener's end of the connection.

    The handler waits for its upstream then and the request has been read whole, so that end
    turns readable only when the reset arrives.
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


@pytest.mark.parametrize(
    ("reads", "held", "content_type", "sent"),
    [
        ((_COMPLETED_JSON,), 0, "application/json", b""),
        ((_DELTA, _COMPLETED), 1, "text/event-stream", _DELTA),
    ],
    ids=["a response that is not a stream", "a stream after its first event"],
)
def test_a_reset_while_an_admitted_response_is_written_is_named_once(
    proxy: _ObservedServer,
    log_path: Path,
    *,
    reads: tuple[bytes, ...],
    held: int,
    content_type: str,
    sent: bytes,
    mocker: MockerFixture,
) -> None:
    upstream = _HeldResponse(*reads, held=held, content_type=content_type)
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

    _reset(client)
    _await_reset(proxy)
    upstream.release.set()

    assert proxy.handler_ended.wait(_HANDLER_SECONDS)
    lines = _log_lines(log_path)
    events = [_fields(line)["event"] for line in lines]
    admitted = events.index("responses_admitted")
    # The close is named under its request, and nothing follows the release.
    assert events[admitted:] == [
        "responses_admitted",
        "downstream_client_closed",
        "responses_released",
    ]
    assert _fields(lines[admitted + 1]) == {
        "req": "1",
        "event": "downstream_client_closed",
        "provider": "dmxapi",
        "path": _PATH,
    }
    # The handler ends the connection: no error leaves it, and it makes no further pass that
    # would meet the close again.
    assert proxy.errors == []
    assert proxy.passes == 1
