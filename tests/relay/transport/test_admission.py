"""Concurrent admission, control routes and health through real loopback hops."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.request

import pytest

from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import LoopbackTransportFixture
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy


class TestProxyTransport(LoopbackTransportFixture):
    def test_json_depth_failure_returns_local_400_and_keeps_listener_usable(self) -> None:
        body = b'{"input":"private-depth-prompt","metadata":' + b"[" * 100_000 + b"0"
        raw = (
            b"POST /aihubmix/v1/responses HTTP/1.1\r\nHost: localhost\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        success = b'{"id":"resp_after_rejection","status":"completed"}'
        current = b'{"input":"current","store":false}'
        with running_proxy([(200, success)]) as (port, received):
            reply = raw_exchange(port, raw)
            assert reply.startswith(b"HTTP/1.1 400 ")
            assert reply.count(b"HTTP/1.1 ") == 1
            error = json.loads(reply.split(b"\r\n\r\n", 1)[1])["error"]
            assert error["code"] == "provider_portable_projection_rejected"
            assert error["reason"] == "invalid_json"
            assert b"private-depth-prompt" not in reply
            assert received == []
            with request(port, current, path="/aihubmix/v1/responses") as response:
                assert response.read() == success
            assert received == [current]

    def test_same_provider_requests_overlap_without_a_proxy_concurrency_gate(self) -> None:
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        success = b'{"id":"resp_concurrent","status":"completed"}'
        scripted = [
            {
                "status": 200,
                "content_type": "application/json",
                "chunks": [success],
                "started_event": event,
                "release_event": release,
            }
            for event in started
        ]
        results: list[tuple[int, bytes]] = []
        body = b'{"model":"gpt-5.6-terra","stream":false,"input":[]}'

        def send(port: int) -> None:
            with request(port, body, path="/aihubmix/v1/responses") as response:
                results.append((response.status, response.read()))

        with running_proxy(scripted) as (port, received):
            workers = [threading.Thread(target=send, args=(port,)) for _ in started]
            for worker in workers:
                worker.start()
            try:
                assert all(event.wait(2) for event in started)
            finally:
                release.set()
            for worker in workers:
                worker.join(timeout=2)
                assert not worker.is_alive()

        assert len(received) == 2
        assert results == [(200, success), (200, success)]

    def test_loopback_healthz_returns_machine_readable_metrics(self) -> None:
        with (
            running_proxy([]) as (port, _received),
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/healthz"
            ) as response,
        ):
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json"
            status = json.loads(response.read())

        assert "counters" in status
        assert "upstream_classifications" in status
        assert status["last_failure"] is None

    @pytest.mark.parametrize(
        "headers",
        [{"Origin": "https://example.invalid"}, {"Host": "example.invalid"}],
        ids=["browser-origin", "rebound-host"],
    )
    def test_lifecycle_control_refuses_requests_a_browser_page_can_send(
        self, headers: dict[str, str]
    ) -> None:
        with running_proxy([]) as (port, _received):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", "/control/handoff", headers=headers)
                response = connection.getresponse()
                response.read()
            finally:
                connection.close()
            assert response.status == 403

    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    def test_drain_path_is_an_unrouted_path_and_leaves_admission_open(self, method: str) -> None:
        success = b'{"id":"resp_served","status":"completed"}'
        body = json.dumps({"stream": False, "input": []}).encode()
        with running_proxy([(200, success)]) as (port, received):
            replies = []
            for path in ("/control/drain", "/control/unrouted"):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    replies.append((response.status, json.loads(response.read())))
                finally:
                    connection.close()
            assert replies[0] == replies[1]
            status, payload = replies[0]
            assert status == 404
            assert payload["error"]["code"] == "provider_route_not_found"

            with request(port, body) as response:
                assert response.read() == success
            assert len(received) == 1

        health = proxy.runtime_status()
        assert (health["accepting"], health["draining"]) == (True, False)
