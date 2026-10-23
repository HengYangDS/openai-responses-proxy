"""Concurrent admission, drain leases and health through real loopback hops."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from typing import cast

import pytest

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.fixtures import LoopbackTransportFixture
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy


class TestProxyTransport(LoopbackTransportFixture):
    def test_json_depth_failure_returns_local_400_and_keeps_listener_usable(self):
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

    def test_same_provider_requests_overlap_without_a_proxy_concurrency_gate(self):
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

    def test_loopback_healthz_returns_machine_readable_metrics(self):
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
    def test_lifecycle_control_refuses_requests_a_browser_page_can_send(self, headers):
        with running_proxy([]) as (port, _received):
            for method, path in (
                ("POST", "/control/drain"),
                ("DELETE", "/control/drain"),
                ("POST", "/control/handoff"),
            ):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request(method, path, headers=headers)
                    response = connection.getresponse()
                    response.read()
                finally:
                    connection.close()
                assert response.status == 403, (method, path)
            assert not proxy.runtime_status()["draining"]

    def test_loopback_drain_rejects_new_responses_and_can_be_reopened(self):
        success = b'{"id":"resp_served","status":"completed"}'
        body = json.dumps({"stream": False, "input": []}).encode()
        with running_proxy([(200, success)]) as (port, received):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            drain = urllib.request.Request(
                f"http://127.0.0.1:{port}/control/drain",
                method="POST",
            )
            with opener.open(drain) as response:
                snapshot = json.loads(response.read())
            assert snapshot["draining"]
            assert snapshot["active_responses"] == 0

            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value:
                assert raised.value.code == 503
                assert raised.value.headers["Retry-After"] == "1"
                payload = json.loads(raised.value.read())
            assert payload["error"]["code"] == "proxy_draining"
            assert received == []

            reopen = urllib.request.Request(
                f"http://127.0.0.1:{port}/control/drain",
                method="DELETE",
            )
            with opener.open(reopen) as response:
                assert not json.loads(response.read())["draining"]
            with request(port, body) as response:
                assert response.read() == success

        status = proxy.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["responses_rejected_while_draining"] == 1
        assert not status["draining"]

    def test_drain_lease_expires_without_a_controller_rollback_request(self, *, mocker):
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        mocker.patch.object(admission.time, "monotonic", side_effect=[10.0, 10.0, 12.1, 12.1, 12.1])
        started = admission.set_draining(True, lease_seconds=2)
        expired = proxy.runtime_status()
        assert started["draining"]
        assert not expired["draining"]
        assert expired["drain_lease_remaining_seconds"] is None
        counters = cast("dict[str, int]", expired["counters"])
        assert counters["drain_leases_expired"] == 1

    def test_drain_closes_admission_while_an_existing_response_finishes(self):
        success = b'{"id":"resp_served","status":"completed"}'
        body = json.dumps({"stream": False, "input": []}).encode()
        started = threading.Event()
        release = threading.Event()
        worker_result = {}

        scripted = {
            "status": 200,
            "content_type": "application/json",
            "chunks": [success],
            "started_event": started,
            "release_event": release,
        }
        with running_proxy([scripted]) as (port, received):

            def request_in_flight():
                try:
                    with request(port, body) as response:
                        worker_result["body"] = response.read()
                except BaseException as exc:  # asserted below; never hide a worker failure
                    worker_result["error"] = exc

            worker = threading.Thread(target=request_in_flight)
            worker.start()
            try:
                assert started.wait(timeout=2), (
                    "upstream never received the first Responses request"
                )

                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                drain = urllib.request.Request(
                    f"http://127.0.0.1:{port}/control/drain",
                    method="POST",
                )
                with opener.open(drain) as response:
                    snapshot = json.loads(response.read())
                assert snapshot["draining"]
                assert snapshot["active_responses"] == 1

                with pytest.raises(urllib.error.HTTPError) as raised:
                    request(port, body)
                with raised.value:
                    assert raised.value.code == 503
                    assert json.loads(raised.value.read())["error"]["code"] == "proxy_draining"
                assert json.loads(received[0]) == {**json.loads(body), "store": False}

                release.set()
                worker.join(timeout=3)
                assert not worker.is_alive(), "in-flight request did not complete after drain"
                assert "error" not in worker_result
                assert worker_result["body"] == success

                with opener.open(f"http://127.0.0.1:{port}/healthz") as response:
                    drained = json.loads(response.read())
                assert drained["draining"]
                assert drained["active_responses"] == 0
            finally:
                release.set()
