"""Empty-response cooldown, secret-free telemetry and disconnect release."""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from openai_responses_proxy.providers.policies import dmxapi as policy
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.empty_response.fixtures import EMPTY_RESPONSE
from tests.relay.empty_response.fixtures import SUCCESS
from tests.relay.empty_response.fixtures import EmptyResponseFixture
from tests.relay.fixtures import serve_proxy


class EmptyResponseTransportTests(EmptyResponseFixture):
    def test_failed_recovery_cools_identical_request_without_upstream_replay(self):
        body = self._body({"stream": False, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 8, tmp)
            try:
                first = self._read_http_error(port, body)
                second = self._read_http_error(port, body)
            finally:
                cleanup()

        assert first[0] == 503
        assert second[0] == 503
        assert json.loads(second[2])["error"]["attempts"] == 0
        assert len(received) == 2
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_cooldown_hits"] == 1

    def test_cooldown_ttl_capacity_and_concurrent_access_are_bounded(self):
        first = self._body(
            {
                "previous_response_id": "first",
                "input": [{"type": "message", "role": "user", "content": "same"}],
            }
        )
        second = self._body(
            {
                "previous_response_id": "second",
                "input": [{"type": "message", "role": "user", "content": "same"}],
            }
        )
        first_key = policy.request_fingerprint(first)
        second_key = policy.request_fingerprint(second)
        self._remember_failure(first_key, now=10.0)
        assert self._cooldown_remaining(first_key, now=10.1) > 0
        assert self._cooldown_remaining(second_key, now=10.1) == 0

        self._remember_failure("expires", now=100.0)
        assert self._cooldown_remaining("expires", now=100.1) > 0
        assert cooldown.has_failure_for_test("expires")
        past_ttl = 100.0 + policy.FAILURE_COOLDOWN_SECONDS + 0.1
        assert self._cooldown_remaining("expires", now=past_ttl) == 0
        # The TTL purge is a real eviction, not merely a read-time comparison:
        # the reading call above must have removed the expired entry itself.
        assert not cooldown.has_failure_for_test("expires")

        # A stale entry is also purged by the next *write* to an unrelated key.
        self._remember_failure("stale", now=300.0)
        self._remember_failure("other", now=300.0 + policy.FAILURE_COOLDOWN_SECONDS + 0.1)
        assert not cooldown.has_failure_for_test("stale")
        assert cooldown.has_failure_for_test("other")

        def remember(index):
            key = f"key-{index}"
            self._remember_failure(key, now=200.0 + index / 10_000)
            return self._cooldown_remaining(key, now=200.0)

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(remember, range(policy.FAILURE_CACHE_CAPACITY + 100)))
        assert cooldown.failure_count_for_test() <= policy.FAILURE_CACHE_CAPACITY
        assert not cooldown.has_failure_for_test("key-0")

    def test_successful_fallback_does_not_enter_cooldown_and_metrics_are_secret_free(
        self,
    ):
        secret = "private-prompt-must-not-appear"
        body = self._body(
            {
                "stream": False,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": secret}],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            port, _received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), (200, SUCCESS)], tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        key = policy.request_fingerprint(body)
        assert self._cooldown_remaining(key) == 0
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_attempts"] == 1
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_accepted"] == 1
        assert secret not in json.dumps(status)
        assert key not in json.dumps(status)

    def test_downstream_disconnect_releases_response_slot(self):
        body = self._body(
            {
                "previous_response_id": "stale",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        started, release = threading.Event(), threading.Event()
        scripted = {
            "status": 200,
            "chunks": [b'data: {"type":"response.completed"}\n\n'],
            "started_event": started,
            "release_event": release,
        }
        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), scripted], tmp)
            try:
                sock = socket.create_connection(("127.0.0.1", port))
                sock.sendall(
                    b"\r\n".join(
                        (
                            b"POST /dmxapi/v1/responses HTTP/1.1",
                            f"Host: 127.0.0.1:{port}".encode(),
                            f"Content-Length: {len(body)}".encode(),
                            b"Connection: close",
                            b"",
                            body,
                        )
                    )
                )
                assert started.wait(timeout=10)
                with contextlib.suppress(OSError):
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0),
                    )
                sock.close()
                release.set()
                for _ in range(50):
                    active = proxy.runtime_status()["active_responses"]
                    if not active:
                        break
                    time.sleep(0.1)
            finally:
                cleanup()

        assert len(received) == 2
        assert proxy.runtime_status()["active_responses"] == 0
        assert self._cooldown_remaining(policy.request_fingerprint(body)) == 0
