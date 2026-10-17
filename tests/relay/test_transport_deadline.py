"""Real HTTP framing cannot renew an admitted exchange's deadline."""

from __future__ import annotations

import json
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import proxy_is_up


class TestTransportDeadline(InputTransportFixture):
    @pytest.fixture
    def tls_context(self, tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
        certificate = tmp_path / "certificate.pem"
        key = tmp_path / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
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
            timeout=10,
        )
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
