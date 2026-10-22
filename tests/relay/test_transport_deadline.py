"""Real HTTP framing cannot renew an admitted exchange's deadline."""

from __future__ import annotations

import errno
import json
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from typing import override

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import proxy_is_up


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Create one loopback identity per module; a cold hosted Windows openssl can exceed 10 s."""
    directory = tmp_path_factory.mktemp("tls")
    certificate = directory / "certificate.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-batch",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
        timeout=60,
    )
    return certificate, key


class TestTransportDeadline(InputTransportFixture):
    @pytest.mark.parametrize(
        ("idle_timeout", "deadline_bound"),
        [(None, True), (0.02, True), (1.0, True), (0.005, False)],
        ids=["deadline-only", "equal-limits", "deadline-shorter", "idle-shorter"],
    )
    def test_native_read_timeout_keeps_its_selected_budget_identity(
        self, idle_timeout, deadline_bound, *, mocker
    ):
        left, right = socket.socketpair()
        with left, right:
            left.settimeout(idle_timeout)
            raw = left.makefile("rb", buffering=0)
            with upstream_transport._DeadlineReader(raw, left, 100.02) as reader:
                response = cast(
                    "upstream_transport.UpstreamResponse",
                    SimpleNamespace(fp=SimpleNamespace(raw=reader)),
                )
                if idle_timeout is not None:
                    upstream_transport.set_read_timeout(response, idle_timeout)
                # Model an OS timeout arriving before the Python deadline clock
                # advances; keep the actual socket and SocketIO timeout native.
                mocker.patch.object(time, "monotonic", return_value=100.0)
                expected = (
                    upstream_transport.ExchangeDeadlineError if deadline_bound else TimeoutError
                )

                with pytest.raises(expected) as raised:
                    reader.readinto(bytearray(1))

                assert type(raised.value) is expected
                if deadline_bound:
                    assert type(raised.value.__cause__) is TimeoutError
                else:
                    assert raised.value.__cause__ is None

        assert raw.closed
        assert reader.closed
        assert left.fileno() == right.fileno() == -1

    @pytest.mark.parametrize("interrupt", [False, True], ids=["last-os-error", "interrupt"])
    def test_connection_setup_failure_releases_its_source_bound_socket(self, interrupt, *, mocker):
        original_socket = socket.socket
        sockets = []
        bindings = []
        original_bind = socket.socket.bind

        def bind(sock, address):
            bindings.append(address)
            original_bind(sock, address)

        def create(*args, **kwargs):
            sock = original_socket(*args, **kwargs)
            sockets.append(sock)
            return sock

        mocker.patch.object(socket, "socket", side_effect=create)
        mocker.patch.object(original_socket, "bind", new=bind)
        # OS refusal and deadline expiry are distinct branches. Keep socket
        # allocation, source binding and teardown real without relying on the
        # platform's scheduling of a closed loopback port.
        failure = KeyboardInterrupt if interrupt else OSError(errno.ECONNREFUSED, "fixture refusal")
        mocker.patch.object(original_socket, "connect", side_effect=failure)
        try:
            with pytest.raises(KeyboardInterrupt if interrupt else OSError) as caught:
                upstream_transport._connect(
                    ("127.0.0.1", 1), 1, ("127.0.0.1", 0), deadline=time.monotonic() + 1
                )

            if not interrupt:
                assert isinstance(caught.value, OSError)
                assert caught.value.errno == errno.ECONNREFUSED
            assert bindings == [("127.0.0.1", 0)]
            assert len(sockets) == 1
            assert sockets[0].fileno() == -1
        finally:
            for sock in sockets:
                sock.close()

    def test_empty_dns_answer_opens_no_socket(self, *, mocker):
        mocker.patch.object(socket, "getaddrinfo", return_value=[])
        create = mocker.patch.object(socket, "socket")

        with pytest.raises(OSError, match="no stream address"):
            upstream_transport._connect(
                ("upstream.invalid", 80), 1, None, deadline=time.monotonic() + 1
            )

        create.assert_not_called()

    def test_expired_dns_result_cannot_start_a_tcp_connection(self, *, mocker):
        upstream = ScriptedUpstream()
        upstream.push(lambda handler: handler.connection.sendall(b"HTTP/1.1 200 OK\r\n\r\n"))
        original_resolve = socket.getaddrinfo
        now = [time.monotonic()]

        def resolve(*args, **kwargs):
            result = original_resolve(*args, **kwargs)
            now[0] += 0.4
            return result

        upstream.start()
        try:
            mocker.patch.object(time, "monotonic", side_effect=lambda: now[0])
            mocker.patch.object(socket, "getaddrinfo", side_effect=resolve)
            mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.25)
            providers = provider_registry.Registry(
                {"fixture": provider_registry.Profile("fixture", upstream.base_url() + "/v1")}
            )
            handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")

            responses.relay(handler, "POST", providers)

            assert handler.statuses == [504]
            assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
            assert upstream.received == []
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            upstream.close()
            assert not upstream.thread.is_alive()

    def test_address_fallback_uses_only_the_remaining_setup_budget(self, *, mocker):
        now = [time.monotonic()]
        sockets = []
        budgets = []

        class UnreachableSocket(socket.socket):
            @override
            def connect(self, address):
                del address
                budget = self.gettimeout()
                assert budget is not None
                budgets.append(budget)
                now[0] += min(0.16, budget)
                raise TimeoutError("fixture connection failed")

        def new_socket(*args, **kwargs):
            sock = UnreachableSocket(*args, **kwargs)
            sockets.append(sock)
            return sock

        mocker.patch.object(time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(socket, "socket", side_effect=new_socket)
        mocker.patch.object(
            socket,
            "getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 9)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 9)),
            ],
        )
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.25)
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "http://upstream.invalid/v1")}
        )
        handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")

        responses.relay(handler, "POST", providers)

        assert handler.statuses == [504]
        assert len(budgets) == 2
        assert budgets[0] == pytest.approx(0.25)
        assert budgets[1] == pytest.approx(0.09)
        assert all(sock.fileno() == -1 for sock in sockets)
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize("handshake_delay", [0.0, 0.15], ids=["immediate", "scheduled"])
    def test_verified_tls_cannot_refund_time_spent_connecting(
        self, tls_context, handshake_delay: float, *, mocker
    ):
        upstream = ScriptedUpstream()
        server_context, client_context = tls_context
        upstream.server.socket = server_context.wrap_socket(
            upstream.server.socket, server_side=True
        )
        payload = b'{"object":"response","status":"completed","output":[]}'
        upstream.push(
            lambda handler: handler.connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nContent-Type: application/json\r\n\r\n%s"
                % (len(payload), payload)
            )
        )
        original_connect = socket.socket.connect
        original_wrap = client_context.wrap_socket
        original_server_wrap = server_context.wrap_socket
        now = [time.monotonic()]
        tls_budgets = []

        def connect(sock, address):
            original_connect(sock, address)
            now[0] += 3.2

        def wrap(sock, *args, **kwargs):
            tls_budgets.append(sock.gettimeout())
            return original_wrap(sock, *args, **kwargs)

        def server_wrap(sock, *args, **kwargs):
            time.sleep(handshake_delay)
            return original_server_wrap(sock, *args, **kwargs)

        upstream.start()
        try:
            mocker.patch.object(time, "monotonic", side_effect=lambda: now[0])
            mocker.patch.object(socket.socket, "connect", new=connect)
            mocker.patch.object(client_context, "wrap_socket", side_effect=wrap)
            mocker.patch.object(server_context, "wrap_socket", side_effect=server_wrap)
            mocker.patch.object(upstream_exchange, "_TLS_CONTEXT", client_context)
            # This case proves budget arithmetic with real verified TLS, not
            # runner scheduling. Keep wall-clock expiry in the stalled-TLS case.
            mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 5.0)
            base_url = upstream.base_url().replace("http://", "https://")
            providers = provider_registry.Registry(
                {"fixture": provider_registry.Profile("fixture", base_url + "/v1")}
            )
            handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")

            responses.relay(handler, "POST", providers)

            assert handler.statuses == [200]
            assert len(upstream.received) == 1
            assert tls_budgets == [pytest.approx(1.8)]
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            upstream.close()
            assert not upstream.thread.is_alive()

    def test_stalled_tls_handshake_closes_within_the_remaining_wall_clock_budget(self, *, mocker):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        release = threading.Event()
        accepted = threading.Event()

        def hold_handshake():
            peer, _ = listener.accept()
            with peer:
                accepted.set()
                release.wait(timeout=3)

        peer_thread = threading.Thread(target=hold_handshake)
        peer_thread.start()
        original_connect = socket.socket.connect

        def connect(sock, address):
            original_connect(sock, address)
            time.sleep(0.25)

        mocker.patch.object(socket.socket, "connect", new=connect)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.4)
        providers = provider_registry.Registry(
            {
                "fixture": provider_registry.Profile(
                    "fixture", f"https://127.0.0.1:{listener.getsockname()[1]}/v1"
                )
            }
        )
        handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")
        try:
            begin = time.monotonic()

            responses.relay(handler, "POST", providers)

            elapsed = time.monotonic() - begin
            assert accepted.is_set()
            assert handler.statuses == [504]
            assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
            assert elapsed < 0.55, f"TLS renewed a 0.4s exchange to {elapsed:.3f}s"
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            release.set()
            peer_thread.join(timeout=3)
            listener.close()
            assert not peer_thread.is_alive()
            assert listener.fileno() == -1

    def test_request_body_cannot_refund_time_spent_sending_headers(self, *, mocker):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        release = threading.Event()

        def consume_request():
            peer, _ = listener.accept()
            with peer:
                release.wait(timeout=3)

        peer_thread = threading.Thread(target=consume_request)
        peer_thread.start()
        original_send = socket.socket.sendall
        now = [time.monotonic()]
        budgets = []

        def send(sock, data, *args, **kwargs):
            if data.startswith(b"POST "):
                original_send(sock, data, *args, **kwargs)
                now[0] += 0.16
            else:
                budgets.append(sock.gettimeout())
                now[0] += 0.25
                raise TimeoutError("fixture request body stalled")

        mocker.patch.object(time, "monotonic", side_effect=lambda: now[0])
        mocker.patch.object(socket.socket, "sendall", new=send)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.25)
        providers = provider_registry.Registry(
            {
                "fixture": provider_registry.Profile(
                    "fixture", f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
                )
            }
        )
        handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")
        try:
            responses.relay(handler, "POST", providers)

            assert handler.statuses == [504]
            assert budgets == [pytest.approx(0.09)]
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            release.set()
            peer_thread.join(timeout=3)
            listener.close()
            assert not peer_thread.is_alive()
            assert listener.fileno() == -1

    @pytest.fixture
    def tls_context(self, tls_material: tuple[Path, Path]) -> tuple[ssl.SSLContext, ssl.SSLContext]:
        certificate, key = tls_material
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(certificate, key)
        return server, ssl.create_default_context(cafile=str(certificate))

    @pytest.mark.parametrize(
        "phase", ["headers", "chunk-size", "chunk-trailer", "stream-chunk-size"]
    )
    @pytest.mark.parametrize("tls", [False, True], ids=["http", "https"])
    def test_progressing_http_framing_cannot_renew_the_deadline(
        self, phase: str, tls: bool, tls_context, *, mocker
    ):
        upstream = ScriptedUpstream()
        release = threading.Event()
        finished = threading.Event()
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        stream = phase == "stream-chunk-size"
        if tls:
            server_context, client_context = tls_context
            upstream.server.socket = server_context.wrap_socket(
                upstream.server.socket, server_side=True
            )
            mocker.patch.object(upstream_exchange, "_TLS_CONTEXT", client_context)

        def slow_framing(handler):
            try:
                if phase == "headers":
                    handler.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Progress: ")
                else:
                    content_type = b"text/event-stream" if stream else b"application/json"
                    handler.connection.sendall(
                        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: "
                        + content_type
                        + b"\r\n\r\n"
                    )
                    if stream:
                        handler.connection.sendall(b"%X\r\n%s\r\n" % (len(content), content))
                    elif phase == "chunk-trailer":
                        handler.connection.sendall(b"2\r\n{}\r\n0\r\nX-Trailer: ")
                    if phase != "chunk-trailer":
                        handler.connection.sendall(b"2;progress=")
                for _ in range(16):
                    if release.wait(timeout=0.08):
                        return
                    handler.connection.sendall(b"x")
                ending = (
                    b"\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}"
                    if phase == "headers"
                    else b"\r\n\r\n"
                    if phase == "chunk-trailer"
                    else b"\r\n{}\r\n0\r\n\r\n"
                )
                handler.connection.sendall(ending)
            finally:
                finished.set()

        upstream.push(slow_framing)
        base_url = (
            upstream.base_url().replace("http://", "https://") if tls else upstream.base_url()
        )
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", base_url + "/v1")}
        )
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.25)
        handler = MemoryHandler(b'{"input":"current","stream":true}', path="/fixture/v1/responses")
        upstream.start()
        try:
            begin = time.monotonic()

            responses.relay(handler, "POST", providers)

            elapsed = time.monotonic() - begin
            if stream:
                assert handler.statuses == [200]
                assert handler.output() == b"%X\r\n%s\r\n" % (len(content), content)
                assert handler.close_connection
            else:
                assert handler.statuses == [504]
                assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
            assert elapsed < 0.65, f"{phase} extended a 0.25s exchange to {elapsed:.3f}s"
            assert len(upstream.received) == 1
            assert self._status_snapshot()["active_responses"] == 0
        finally:
            release.set()
            upstream.close()
            assert finished.wait(timeout=2)
            assert not upstream.thread.is_alive()
            assert not proxy_is_up(upstream.port)
