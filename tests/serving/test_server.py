"""Listener bindings, request routing and connection lifetime contracts."""

from __future__ import annotations

import dataclasses
import socket

import pytest

from openai_responses_proxy.relay import responses
from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import server


def test_server_bindings_are_required_and_immutable(*, mocker) -> None:
    providers = mocker.sentinel.providers
    bindings = server.Bindings(
        control=control.Bindings(mocker.Mock(return_value={}), mocker.Mock()),
        providers=providers,
        server_version="dmx/test",
    )
    assert bindings.providers is providers
    attribute = "server_version"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(bindings, attribute, "dmx/other")


def test_server_routes_each_method_to_one_owner(subtests, *, mocker) -> None:
    handler = object.__new__(server.Handler)
    bindings = control.Bindings(mocker.Mock(return_value={}), mocker.Mock())
    providers = mocker.sentinel.providers
    handler.server = mocker.Mock(
        bindings=server.Bindings(control=bindings, providers=providers, server_version="dmx/test")
    )
    drain = mocker.patch.object(control, "set_drain")
    handoff = mocker.patch.object(control, "prepare_handoff")
    status = mocker.patch.object(control, "send_status")
    relay = mocker.patch.object(responses, "relay")
    for method, path, owner, arguments in (
        ("do_POST", "/control/drain", drain, (True,)),
        ("do_POST", "/control/handoff", handoff, (bindings,)),
        ("do_POST", "/v1/responses", relay, ("POST", providers)),
        ("do_GET", "/healthz", status, (bindings,)),
        ("do_GET", "/v1/responses", relay, ("GET", providers)),
        ("do_DELETE", "/control/drain", drain, (False,)),
        ("do_DELETE", "/v1/responses/resp", relay, ("DELETE", providers)),
        ("do_PATCH", "/v1/responses/resp", relay, ("PATCH", providers)),
        ("do_PUT", "/v1/responses", relay, ("PUT", providers)),
    ):
        with subtests.test(method=method, path=path):
            for candidate in (drain, handoff, status, relay):
                candidate.reset_mock()
            handler.path = path
            getattr(handler, method)()
            owner.assert_called_once_with(handler, *arguments)


def test_server_hardening_and_handoff_listener_adoption(*, mocker) -> None:
    connection = mocker.Mock()
    server._disable_nagle(connection)
    connection.setsockopt.assert_called_once_with(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    connection.setsockopt.side_effect = OSError
    server._disable_nagle(connection)
    mocker.patch.object(socket, "getfqdn", side_effect=AssertionError("DNS lookup"))
    created = server.ResilientProxyServer(
        ("127.0.0.1", 0), server.Handler, mocker.sentinel.bindings
    )
    try:
        assert created.server_name == "127.0.0.1"
        assert created.server_port == created.server_address[1]
    finally:
        created.server_close()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    mocker.patch.object(socket, "getfqdn", side_effect=AssertionError("DNS lookup"))
    adopted = server.server_from_listener(listener, mocker.sentinel.bindings)
    assert adopted.socket is listener
    assert adopted.server_address == listener.getsockname()
    assert adopted.server_name == "127.0.0.1"
    assert adopted.server_port == adopted.server_address[1]
    adopted.server_close()


def test_server_tracks_handlers_and_classifies_disconnects(*, mocker) -> None:
    proxy = object.__new__(server.ResilientProxyServer)
    process = mocker.patch("http.server.ThreadingHTTPServer.process_request_thread")
    begin = mocker.patch.object(server.admission, "begin_handler")
    end = mocker.patch.object(server.admission, "end_handler")
    server.ResilientProxyServer.process_request_thread(proxy, None, None)
    begin.assert_called_once()
    process.assert_called_once()
    end.assert_called_once()
    process.side_effect = RuntimeError
    with pytest.raises(RuntimeError):
        server.ResilientProxyServer.process_request_thread(proxy, None, None)
    assert end.call_count == 2
    log = mocker.patch.object(server.operational_log, "log")
    try:
        raise BrokenPipeError("private-client-context")
    except BrokenPipeError:
        server.ResilientProxyServer.handle_error(proxy, None, None)
    log.assert_called_once_with("event=client_closed_mid_request exception=BrokenPipeError")
    fallback = mocker.patch("http.server.ThreadingHTTPServer.handle_error")
    try:
        raise RuntimeError("failed")
    except RuntimeError:
        server.ResilientProxyServer.handle_error(proxy, None, None)
    fallback.assert_called_once()
