"""Real rolling handoff, traffic continuity and lease completion."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from openai_responses_proxy.lifecycle.supervision import process
from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import child_pid_observer
from tests.serving.handoff.fixtures import free_port
from tests.serving.handoff.fixtures import http_json
from tests.serving.handoff.fixtures import installed_expected_metadata
from tests.serving.handoff.fixtures import pid_alive
from tests.serving.handoff.fixtures import start_real_proxy
from tests.serving.handoff.fixtures import terminate_process
from tests.serving.handoff.fixtures import wait_until
from tests.serving.handoff.subprocess.fixtures import PACKAGED_SUCCESSOR_TIMEOUT_SECONDS
from tests.serving.handoff.subprocess.fixtures import SubprocessFixture

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestRealSubprocessHandoffIntegration(SubprocessFixture):
    def test_old_pid_serves_before_handoff_and_child_pid_serves_after(self):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        upstream.push((200, b'{"id":"ok","status":"completed"}'))

        port = free_port()
        root, ctx, owned_processes = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        log_path = root / "proxy.log"
        old = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=log_path)
        self._cleanups.callback(lambda: terminate_process(old))
        upstream.start()

        status_code, before = http_json(port, "/healthz")
        assert status_code == 200
        old_runtime_pid = before.get("pid")
        assert type(old_runtime_pid) is int
        assert process.verified_proxy_listener_pids(ctx) == [old_runtime_pid]

        expected = installed_expected_metadata(ctx, "txn-real-1")
        status_code, ready = http_json(
            port, "/control/handoff", method="POST", body=expected, timeout=35
        )
        assert status_code == 202
        assert ready.get("transaction_id") == expected["transaction_id"]
        _child_pid, observe = child_pid_observer(
            port,
            expected,
            exclude_pid=old_runtime_pid,
            owned_processes=owned_processes,
            executable=ctx.executable,
        )

        assert wait_until(observe, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS), (
            "child did not take over serving with matching health"
        )
        assert wait_until(lambda: old.poll() is not None, timeout=10), (
            "old process did not exit after finalize"
        )

    def test_finalized_child_can_drive_a_second_real_handoff(self):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        port = free_port()
        root, ctx, owned_processes = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        old = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(old))
        upstream.start()

        first = installed_expected_metadata(ctx, "txn-repeat-1")
        status_code, _ready = http_json(
            port, "/control/handoff", method="POST", body=first, timeout=35
        )
        assert status_code == 202
        child_one, observe_first = child_pid_observer(
            port,
            first,
            exclude_pid=old.pid,
            owned_processes=owned_processes,
            executable=ctx.executable,
        )

        assert wait_until(observe_first, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS)
        assert wait_until(lambda: old.poll() is not None, timeout=10)

        second = installed_expected_metadata(ctx, "txn-repeat-2")
        second_request = {**second, "lease_seconds": 1, "timeout_seconds": 30}
        status_code, _ready = http_json(
            port,
            "/control/handoff",
            method="POST",
            body=second_request,
            timeout=35,
        )
        assert status_code == 202
        _child_two, observe_second = child_pid_observer(
            port,
            second,
            exclude_pid=child_one["value"],
            owned_processes=owned_processes,
            executable=ctx.executable,
        )

        assert wait_until(observe_second, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS)
        retired = wait_until(lambda: not pid_alive(child_one["value"]), timeout=10)
        child_one_pid = child_one["value"]
        detail = (
            process.process_command(child_one_pid)
            if not retired and isinstance(child_one_pid, int)
            else None
        )
        assert retired, f"first finalized child did not retire after the second handoff: {detail!r}"

    def test_long_upstream_response_completes_during_handoff_while_child_serves_and_old_exits_after(
        self,
    ):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        started = threading.Event()
        release = threading.Event()

        def long_response(handler):
            started.set()
            release.wait(timeout=10)
            payload = b'{"id":"finished-late","status":"completed"}'
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        upstream.push(long_response)

        port = free_port()
        root, ctx, owned_processes = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        log_path = root / "proxy.log"
        old = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=log_path)
        self._cleanups.callback(lambda: terminate_process(old))
        upstream.start()

        held = {}

        def run_holder():
            held["body"] = self._post_responses(port)

        holder = threading.Thread(target=run_holder)
        holder.start()
        self._cleanups.callback(lambda: release.set())
        assert started.wait(timeout=10), (
            "long upstream call did not start on the old process in time"
        )

        expected = installed_expected_metadata(ctx, "txn-real-2")
        status_code, _ready = http_json(
            port, "/control/handoff", method="POST", body=expected, timeout=35
        )
        assert status_code == 202

        _child_pid, observe = child_pid_observer(
            port,
            expected,
            exclude_pid=old.pid,
            owned_processes=owned_processes,
            executable=ctx.executable,
        )

        assert wait_until(observe, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS), (
            "child did not take over serving with matching health"
        )

        # The queue is now empty (the held request already popped its own
        # behavior before blocking on ``release``), so pushing exactly one new
        # behavior now deterministically belongs to the next request only.
        upstream.push((200, b'{"id":"new-via-child","status":"completed"}'))
        new_body = json.loads(self._post_responses(port, timeout=10))
        assert new_body.get("id") == "new-via-child"

        release.set()
        holder.join(timeout=15)
        assert held.get("body") == b'{"id":"finished-late","status":"completed"}'
        assert wait_until(lambda: old.poll() is not None, timeout=10), (
            "old process did not exit after the held response completed"
        )

    def test_bounded_lease_forces_old_to_exit_even_if_a_held_stream_never_finishes(
        self,
    ):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        started = threading.Event()
        never_release = threading.Event()

        def never_finishes(handler):
            started.set()
            never_release.wait(timeout=6)  # bounded so the test itself cannot hang
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            payload = b'{"status":"completed"}'
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        upstream.push(never_finishes)

        port = free_port()
        root, ctx, owned_processes = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        log_path = root / "proxy.log"
        old = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=log_path)
        self._cleanups.callback(lambda: terminate_process(old))
        upstream.start()

        def hold_stream():
            try:
                self._post_responses(port, timeout=8)
            except Exception:
                pass

        holder = threading.Thread(target=hold_stream, daemon=True)
        holder.start()
        self._cleanups.callback(never_release.set)
        assert started.wait(timeout=10), "held stream did not start on the old process in time"

        expected = installed_expected_metadata(ctx, "txn-real-3")
        expected["lease_seconds"] = 1
        status_code, _ready = http_json(
            port, "/control/handoff", method="POST", body=expected, timeout=35
        )
        assert status_code == 202

        child_pid, observe = child_pid_observer(
            port,
            expected,
            exclude_pid=old.pid,
            owned_processes=owned_processes,
            executable=ctx.executable,
        )

        assert wait_until(observe, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS), (
            "child did not take over serving with matching health"
        )

        # Deterministic: the held request's behavior was already popped before
        # it blocked, so this new push belongs solely to the queued request below.
        upstream.push((200, b'{"id":"via-child-while-old-held","status":"completed"}'))
        queued_body = json.loads(self._post_responses(port, timeout=10))
        assert queued_body.get("id") == "via-child-while-old-held"

        assert wait_until(lambda: old.poll() is not None, timeout=10), (
            "old process did not exit despite the lease expiring on a held stream"
        )
        status_code, still_healthy = http_json(port, "/healthz", timeout=3)
        assert status_code == 200
        assert still_healthy.get("pid") == child_pid["value"]
        assert still_healthy.get("accepting") is True

    def test_handoff_completes_after_requesting_controller_disconnects(self):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        port = free_port()
        root, ctx, owned_processes = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        old = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(old))
        upstream.start()

        expected = installed_expected_metadata(ctx, "txn-controller-disconnect")
        raw = json.dumps(expected).encode()
        request = (
            f"POST /control/handoff HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + raw
        with socket.create_connection(("127.0.0.1", port), timeout=3) as controller:
            controller.sendall(request)
            controller.shutdown(socket.SHUT_RDWR)

        child_pid, observe = child_pid_observer(
            port,
            expected,
            exclude_pid=old.pid,
            states=frozenset(("finalized",)),
            owned_processes=owned_processes,
            executable=ctx.executable,
        )
        assert wait_until(observe, timeout=PACKAGED_SUCCESSOR_TIMEOUT_SECONDS), (
            "listener-owned transaction did not survive controller disconnect"
        )
        assert wait_until(lambda: old.poll() is not None, timeout=10)
        status_code, health = http_json(port, "/healthz", timeout=3)
        assert status_code == 200
        assert health.get("pid") == child_pid["value"]
        assert health.get("accepting") is True
