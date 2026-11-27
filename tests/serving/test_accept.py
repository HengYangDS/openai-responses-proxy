"""The accept point stamps each connection, and a listener accepts in the order of connecting.

The bound of a handoff's begin is counted from the stamp, and the controller's wait rests on
the order: a connection that the listener accepts later is stamped later.
"""

from __future__ import annotations

import gc
import socket
import socketserver
from typing import TYPE_CHECKING

from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import server

if TYPE_CHECKING:
    import pytest
    from pytest_mock import MockerFixture

_PEER = ("127.0.0.1", 51234)


def _listener(mocker: MockerFixture) -> server.ResilientProxyServer:
    """Return a bound loopback listener of the product that nothing serves."""
    bindings = server.Bindings(
        control=control.Bindings(mocker.Mock(return_value={}), mocker.Mock()),
        server_version="dmx/test",
    )
    return server.ResilientProxyServer(("127.0.0.1", 0), server.Handler, bindings)


def _accept(
    listener: server.ResilientProxyServer, connection: socket.socket, *, mocker: MockerFixture
) -> socket.socket:
    """Let the listener's accept point take ``connection`` as its next accepted connection."""
    # The queue gives the connection away, so that nothing here keeps it alive.
    pending = [connection]
    mocker.patch.object(
        socketserver.TCPServer, "get_request", side_effect=lambda: (pending.pop(), _PEER)
    )
    request, address = listener.get_request()
    assert address == _PEER
    return request


def test_the_handler_of_a_handoff_request_reads_the_stamp_of_the_accept(
    *, mocker: MockerFixture
) -> None:
    """The stamp is taken where the connection is accepted, not where its request is handled."""
    listener = _listener(mocker)
    connection = socket.socket()
    try:
        clock = mocker.patch.object(server.time, "monotonic", return_value=100.0)
        request = _accept(listener, connection, mocker=mocker)
        assert request is connection
        assert listener.accepted_at(connection) == 100.0

        # The handler's thread runs later, and reads the request later still.
        clock.return_value = 500.0
        handler = object.__new__(server.Handler)
        handler.server = listener
        handler.connection = connection
        handler.path = "/control/handoff"
        prepare = mocker.patch.object(control, "prepare_handoff")
        handler.do_POST()

        prepare.assert_called_once_with(handler, listener.bindings.control, accepted_at=100.0)
        assert listener.accepted_at(connection) == 100.0
    finally:
        connection.close()
        listener.server_close()


def test_each_request_on_a_real_connection_carries_the_stamp_of_that_connections_accept(
    *, mocker: MockerFixture
) -> None:
    """The listener's own accept point and handler on a loopback connection, in one thread.

    The client has sent two handoff requests on one connection before the listener accepts
    it. The handler serves both on the kept-alive connection, and each carries the accept's
    stamp, however late its handler reads it: a later request on a connection is not bounded
    anew, so it is refused once the connection is older than its timeout.
    """
    listener = _listener(mocker)
    request = b"POST /control/handoff HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n"
    client = socket.create_connection(("127.0.0.1", listener.server_port), timeout=10)
    try:
        client.sendall(request + b"\r\n{}" + request + b"Connection: close\r\n\r\n{}")
        listener.socket.settimeout(10)
        clock = mocker.patch.object(server.time, "monotonic", return_value=100.0)
        connection, address = listener.get_request()
        clock.return_value = 500.0
        # The route's owner reads the body of its request, as the product's does.
        prepare = mocker.patch.object(
            control, "prepare_handoff", side_effect=lambda handler, *_a, **_k: handler.rfile.read(2)
        )
        try:
            listener.finish_request(connection, address)
        finally:
            listener.shutdown_request(connection)

        assert [call.kwargs for call in prepare.call_args_list] == [{"accepted_at": 100.0}] * 2
        assert {call.args[0].connection for call in prepare.call_args_list} == {connection}
    finally:
        client.close()
        listener.server_close()


def test_a_connection_that_the_listener_did_not_accept_has_no_stamp(
    *, mocker: MockerFixture
) -> None:
    listener = _listener(mocker)
    foreign = socket.socket()
    try:
        assert listener.accepted_at(foreign) is None
        handler = object.__new__(server.Handler)
        handler.server = listener
        handler.connection = foreign
        handler.path = "/control/handoff"
        prepare = mocker.patch.object(control, "prepare_handoff")
        handler.do_POST()

        prepare.assert_called_once_with(handler, listener.bindings.control, accepted_at=None)
    finally:
        foreign.close()
        listener.server_close()


def test_a_stamp_does_not_outlive_its_connection(*, mocker: MockerFixture) -> None:
    listener = _listener(mocker)
    # The one reference to the connection, which the test gives up below.
    held = [socket.socket()]
    try:
        _accept(listener, held[0], mocker=mocker)
        assert listener.accepted_at(held[0]) is not None
        assert len(listener._accepted) == 1

        # A closed connection that its handler still holds keeps its stamp.
        held[0].close()
        assert listener.accepted_at(held[0]) is not None
        held.clear()
        gc.collect()

        assert len(listener._accepted) == 0
    finally:
        for connection in held:
            connection.close()
        listener.server_close()


def test_a_listener_accepts_connections_in_the_order_in_which_they_connected(
    subtests: pytest.Subtests, *, mocker: MockerFixture
) -> None:
    """The platform's part of the bound, with real loopback sockets and no thread.

    Each client has connected before the next one begins, and nothing accepts meanwhile. The
    listener then takes them from its queue in that order, so each is stamped no earlier than
    the one before it.
    """
    listener = _listener(mocker)
    clients: list[socket.socket] = []
    accepted: list[socket.socket] = []
    try:
        # One blocking connect after another: each has connected before the next begins.
        clients.extend(
            socket.create_connection(("127.0.0.1", listener.server_port), timeout=10)
            for _ in range(8)
        )
        # An absent connection fails the test instead of blocking it.
        listener.socket.settimeout(10)
        for _ in clients:
            request, _address = listener.get_request()
            accepted.append(request)

        with subtests.test("the order of accepting is the order of connecting"):
            assert [request.getpeername() for request in accepted] == [
                client.getsockname() for client in clients
            ]
        with subtests.test("a connection that is accepted later is stamped no earlier"):
            stamps = [listener.accepted_at(request) or 0.0 for request in accepted]
            assert all(stamps)
            assert stamps == sorted(stamps)
    finally:
        for connection in (*accepted, *clients):
            connection.close()
        listener.server_close()
