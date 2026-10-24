"""A client that stops reading cannot hold an exchange past its deadline."""

from __future__ import annotations

import socket
import threading
import time

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import exchange as upstream_exchange
from tests.relay.fixtures import LoopbackTransportFixture
from tests.relay.fixtures import request_body
from tests.relay.fixtures import running_proxy
from tests.serving.handoff.fixtures import wait_until


class TestDownstreamWriteDeadline(LoopbackTransportFixture):
    def test_a_stalled_reader_releases_admission_and_upstream_at_the_deadline(self, *, mocker):
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 1.0)
        delta = b'data: {"type":"response.output_text.delta","delta":"' + b"x" * 65536 + b'"}\n\n'
        held_open = threading.Event()
        body = request_body(stream=True)
        with running_proxy([{"chunks": [delta] * 400, "stall_event": held_open}]) as (
            port,
            received,
        ):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # A tiny receive window makes the proxy's writes block once the client stops reading.
                client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                client.settimeout(5)
                client.connect(("127.0.0.1", port))
                client.sendall(
                    b"POST /dmxapi/v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                    % (len(body), body)
                )
                assert client.recv(1024).startswith(b"HTTP/1.1 200")
                stopped_reading = time.monotonic()

                released = wait_until(lambda: admission.active_responses() == 0, timeout=4)
                elapsed = time.monotonic() - stopped_reading

                assert released, "the stalled reader still holds its admission slot"
                assert elapsed < 2.0
                assert len(received) == 1
            finally:
                held_open.set()
                client.close()
