"""A request the upstream may have accepted is never sent again (BLOCK bde945c5)."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler

COMPLETED = b'{"object":"response","status":"completed","output":[]}'

type Behavior = Callable[[socket.socket], bool]
"""Answer one complete request; return whether the connection stays open."""


def _complete(connection: socket.socket) -> bool:
    connection.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
        % (len(COMPLETED), COMPLETED)
    )
    return True


def _close_after_request(connection: socket.socket) -> bool:
    del connection
    return False


def _created_then_eof(connection: socket.socket) -> bool:
    event = b'data: {"type":"response.created"}\n\n'
    connection.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: %d\r\n\r\n%s"
        % (len(event), event)
    )
    return False


def _failed_before_content(connection: socket.socket) -> bool:
    events = (
        b'data: {"type":"response.created"}\n\n'
        b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
    )
    connection.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: %d\r\n\r\n%s"
        % (len(events), events)
    )
    return True


def _read_request(connection: socket.socket) -> bytes | None:
    """Read one complete request with a Content-Length body, or None at end of stream."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(65536)
        if not chunk:
            return None
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    while len(body) < length:
        chunk = connection.recv(65536)
        if not chunk:
            return None
        body += chunk
    return body


class _Upstream:
    """Answer each complete request with the next behavior and record its connection."""

    def __init__(self, behaviors: list[Behavior]) -> None:
        self._behaviors = behaviors
        self._lock = threading.Lock()
        self.requests: list[int] = []
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._threads: list[threading.Thread] = []
        accept = threading.Thread(target=self._accept, daemon=True)
        accept.start()
        self._threads.append(accept)

    def _accept(self) -> None:
        index = 0
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            index += 1
            serve = threading.Thread(target=self._serve, args=(connection, index), daemon=True)
            serve.start()
            self._threads.append(serve)

    def _serve(self, connection: socket.socket, index: int) -> None:
        with connection:
            while _read_request(connection) is not None:
                with self._lock:
                    self.requests.append(index)
                    behavior = self._behaviors.pop(0) if self._behaviors else _complete
                if not behavior(connection):
                    return

    def close(self) -> None:
        self._listener.close()
        for thread in self._threads:
            thread.join(timeout=5)


@contextmanager
def _upstream(
    behaviors: list[Behavior],
) -> Generator[tuple[_Upstream, Callable[[bytes], MemoryHandler]]]:
    upstream = _Upstream(behaviors)
    providers = provider_registry.Registry(
        {"fixture": provider_registry.Profile("fixture", f"http://127.0.0.1:{upstream.port}/v1")}
    )

    def call(body: bytes) -> MemoryHandler:
        handler = MemoryHandler(body, path="/fixture/v1/responses")
        responses.relay(handler, "POST", providers)
        return handler

    try:
        yield upstream, call
    finally:
        upstream_transport.reset_for_test()
        upstream.close()


def _error(handler: MemoryHandler) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(handler.output())["error"])


class TestRequestIsSentAtMostOnce(InputTransportFixture):
    def test_a_pooled_connection_lost_after_the_request_is_not_resent(self, *, mocker):
        sleep = mocker.patch.object(upstream_exchange.time, "sleep")
        with _upstream([_complete, _close_after_request]) as (upstream, call):
            warm = call(b'{"input":"warm"}')
            fault = call(b'{"input":"current"}')
        assert warm.statuses == [200]
        assert fault.statuses == [502]
        assert _error(fault)["code"] == "upstream_outcome_unknown"
        assert upstream.requests == [1, 1]
        sleep.assert_not_called()
        assert self._status_snapshot()["active_responses"] == 0

    def test_a_new_connection_lost_after_the_request_is_not_resent(self, *, mocker):
        sleep = mocker.patch.object(upstream_exchange.time, "sleep")
        with _upstream([_close_after_request]) as (upstream, call):
            fault = call(b'{"input":"current"}')
        assert fault.statuses == [502]
        assert _error(fault)["code"] == "upstream_outcome_unknown"
        assert upstream.requests == [1]
        sleep.assert_not_called()

    def test_a_refused_connection_is_retried_because_nothing_was_sent(self, *, mocker):
        sleep = mocker.patch.object(upstream_exchange.time, "sleep")
        with socket.create_server(("127.0.0.1", 0)) as probe:
            port = probe.getsockname()[1]
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", f"http://127.0.0.1:{port}/v1")}
        )
        handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")
        responses.relay(handler, "POST", providers)
        assert handler.statuses == [502]
        assert _error(handler)["code"] == "upstream_transport_error"
        assert sleep.call_count == upstream_exchange._MAX_ATTEMPTS - 1

    def test_a_stream_that_ends_before_content_without_a_failure_is_not_reopened(self, *, mocker):
        sleep = mocker.patch.object(sse.time, "sleep")
        with _upstream([_created_then_eof]) as (upstream, call):
            fault = call(b'{"input":"current","stream":true}')
        assert fault.statuses == [502]
        assert _error(fault)["code"] == "upstream_outcome_unknown"
        assert upstream.requests == [1]
        sleep.assert_not_called()
        counters, _ = self._status_maps()
        assert counters["streams_pre_content_reconnect_attempts"] == 0
        assert counters["upstream_outcome_unknown"] == 1

    def test_a_reopened_stream_request_lost_after_sending_is_not_resent(self, *, mocker):
        mocker.patch.object(sse.time, "sleep")
        with _upstream([_failed_before_content, _close_after_request]) as (upstream, call):
            fault = call(b'{"input":"current","stream":true}')
        assert fault.statuses == [502]
        error = _error(fault)
        assert error["code"] == "upstream_outcome_unknown"
        assert error["reason"] == "response_not_received"
        assert error["attempts"] == 2
        assert len(upstream.requests) == 2

    @pytest.mark.parametrize("attempt", [0, upstream_exchange._MAX_ATTEMPTS - 1])
    def test_an_outcome_unknown_failure_is_terminal_at_every_attempt(self, attempt, *, mocker):
        sleep = mocker.patch.object(upstream_exchange.time, "sleep")
        handler = MemoryHandler(b'{"input":"current"}')
        exchange = upstream_exchange.Exchange(
            handler=handler,
            method="POST",
            request_id=1,
            body=b'{"input":"current"}',
            url="https://upstream.invalid/v1/responses",
            headers={},
            is_responses=True,
            attempt_body=b'{"input":"current"}',
            profile=provider_registry.load().profiles["dmxapi"],
        )
        error = upstream_transport.UpstreamOutcomeUnknownError()
        assert upstream_exchange._transport_error(exchange, error, attempt) == "terminal"
        assert handler.statuses == [502]
        assert _error(handler)["code"] == "upstream_outcome_unknown"
        sleep.assert_not_called()
