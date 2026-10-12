"""Real loopback subprocess integration for rolling handoff."""

from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from tests.release.fixtures import preserve_native_host_projection
from tests.service.handoff import fixtures as handoff_fixtures
from tests.service.handoff.fixtures import ScriptedUpstream
from tests.service.handoff.fixtures import child_pid_observer
from tests.service.handoff.fixtures import free_port
from tests.service.handoff.fixtures import http_json
from tests.service.handoff.fixtures import installed_expected_metadata
from tests.service.handoff.fixtures import pid_alive
from tests.service.handoff.fixtures import start_real_proxy
from tests.service.handoff.fixtures import terminate_owned_proxy
from tests.service.handoff.fixtures import terminate_process
from tests.service.handoff.fixtures import wait_until
from tests.service.handoff.fixtures import write_installed_payload

ROOT = Path(__file__).resolve().parents[3]

PACKAGED_SUCCESSOR_TIMEOUT_SECONDS = 60
PAYLOAD_UNLOCK_TIMEOUT_SECONDS = 5

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestRealSubprocessHandoffIntegration:
    """Exercise the complete rolling handoff against owned loopback processes."""

    def setup_method(self) -> None:
        self._cleanups = ExitStack()

    def teardown_method(self) -> None:
        self._cleanups.close()

    @pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
    def test_native_listener_preserves_tool_deliveries_and_encrypted_agent_tasks(self, call_type):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        completed = b'data: {"type":"response.completed"}\n\n'

        def send(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(completed)))
            handler.end_headers()
            handler.wfile.write(completed)

        upstream.push(send)
        port = free_port()
        root, ctx, _owned = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(child))
        upstream.start()
        argument = "arguments" if call_type == "function_call" else "input"
        call = {"type": call_type, "call_id": "job", "name": "run", argument: "{}"}
        first = {"type": f"{call_type}_output", "call_id": "job", "output": "Running"}
        latest = {"type": "message", "role": "user", "content": "Continue"}
        delivery = {**first, "id": "delivery", "name": "run", "output": "Complete"}
        task = {
            "type": "agent_message",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": [
                {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                {"type": "encrypted_content", "encrypted_content": "fixture-task"},
            ],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=json.dumps(
                {
                    "model": "synthetic",
                    "stream": True,
                    "input": [call, first, latest, delivery, task],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            assert response.read() == completed
        assert len(upstream.received) == 1
        items = json.loads(upstream.received[0])["input"]
        assert items[:3] == [call, first, latest]
        assert items[3] == {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": '{"type":"tool_delivery","call_id":"job","name":"run"}\nComplete',
        }
        assert items[4] == task

    @pytest.mark.parametrize("ending", ["closed", "held_open", "malformed"])
    def test_native_listener_recovers_pre_content_failure_without_duplicate_output(self, ending):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        release = threading.Event()
        self._cleanups.callback(release.set)
        request_id = "64781e82-1e8f-42af-a06a-409e154bbfe2"
        failed = (
            b'data: {"type":"response.created"}\n\n'
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error",'
            b'"message":"request ID ' + request_id.encode() + b'"}}}\n\ndata: [DONE]\n\n'
        )
        completed = (
            b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
            b'data: {"type":"response.completed"}\n\n'
        )
        if ending == "malformed":
            completed = completed.split(b'data: {"type":"response.completed"}')[0]
            completed += b'data: {"type":\n\n'

        def send(handler):
            payload = failed if len(upstream.received) == 1 else completed
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            held = ending == "held_open" and len(upstream.received) == 2
            if held:
                handler.send_header("Transfer-Encoding", "chunked")
            else:
                handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload) if held else payload)
            handler.wfile.flush()
            if held:
                release.wait(timeout=10)
                handler.close_connection = True

        upstream.push(send)
        upstream.push(send)
        port = free_port()
        root, ctx, _owned = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(child))
        upstream.start()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=b'{"model":"synthetic","stream":true,"input":[]}',
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                if ending == "malformed":
                    with pytest.raises(http.client.IncompleteRead) as raised:
                        response.read()
                    assert b'"delta":"recovered"' in raised.value.partial
                    assert b"stream_projection_failed" not in raised.value.partial
                else:
                    assert response.read() == completed
                assert not release.is_set()
        finally:
            release.set()
        assert len(upstream.received) == 2
        assert upstream.received[0] == upstream.received[1]
        logs = (Path(ctx.log_dir) / "proxy.log").read_text(encoding="utf-8")
        assert f"upstream_request_id={request_id}" in logs
        failure = next(line for line in logs.splitlines() if "event=sse_upstream_failed" in line)
        assert "code=server_error" in failure
        assert "committed=False" in failure

    def test_scripted_upstream_starts_only_after_the_proxy_child_is_spawned(
        self,
    ) -> None:
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)

        assert not upstream.thread.is_alive()
        upstream.start()
        assert upstream.thread.is_alive()

    def test_initial_proxy_spawn_avoids_multithreaded_posix_fork(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctx = write_installed_payload(
                root,
                release="1.0.25",
                port=free_port(),
                upstream_url="http://127.0.0.1:43123",
            )
            mocker.patch.dict(
                handoff_fixtures.os.environ,
                {"OPENAI_RESPONSES_PROXY_EXECUTABLE": "/tmp/native-build-source"},
            )
            child = mocker.Mock()
            child.poll.return_value = None
            popen = mocker.patch.object(handoff_fixtures.subprocess, "Popen", return_value=child)
            mocker.patch.object(handoff_fixtures, "proxy_is_up", return_value=True)
            started = start_real_proxy(
                ctx,
                upstream_url="http://127.0.0.1:43123",
                log_path=root / "proxy.log",
            )

        assert started is child
        assert popen.call_args.kwargs.get("close_fds", True) is True
        assert popen.call_args.kwargs["env"]["OPENAI_RESPONSES_PROXY_HOME"] == ctx.install_dir
        assert popen.call_args.kwargs["env"]["OPENAI_RESPONSES_PROXY_STATE_HOME"] == str(
            root / "state"
        )
        assert popen.call_args.kwargs["env"]["OPENAI_RESPONSES_PROXY_EXECUTABLE"] == ctx.executable

    def _installed_fixture(
        self, *, release: str, port: int, upstream_url: str
    ) -> tuple[Path, runtime_context.RuntimeContext, dict[int, process.OwnedProcess]]:
        temporary = tempfile.TemporaryDirectory(delete=False)
        root = Path(temporary.name)
        ctx = write_installed_payload(root, release=release, port=port, upstream_url=upstream_url)
        owned_processes: dict[int, process.OwnedProcess] = {}
        self._cleanups.callback(self._cleanup_installed_fixture, temporary, ctx, owned_processes)
        return root, ctx, owned_processes

    def _cleanup_installed_fixture(
        self,
        temporary: tempfile.TemporaryDirectory[str],
        ctx: runtime_context.RuntimeContext,
        owned_processes: dict[int, process.OwnedProcess],
    ) -> None:
        proxy_script = ctx.executable
        failure: Exception | None = None
        try:
            service = native_service.adapter()
            service.uninstall(ctx)
            assert service.status(ctx) == "absent"
        except Exception as error:
            failure = error
        finally:
            for pid in process.pids_naming_executable(proxy_script):
                owned = process.capture_executable(pid, proxy_script)
                if owned is None:
                    failure = failure or errors.InstallError("test process identity is unproved")
                else:
                    owned_processes[pid] = owned
            for owned in owned_processes.values():
                try:
                    if not process.terminate_owned_process(owned):
                        raise errors.InstallError("test process exit is unproved")
                except errors.InstallError as error:
                    failure = failure or error
        if failure is not None:
            raise failure
        remaining = {
            owned.pid for owned in owned_processes.values() if process.owned_process_alive(owned)
        }
        assert not remaining, f"orphaned proxy children for {proxy_script}: {remaining}"
        deadline = time.monotonic() + PAYLOAD_UNLOCK_TIMEOUT_SECONDS
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def test_fixture_cleanup_terminates_before_removing_temporary_payload(self, *, mocker) -> None:
        events = []
        temporary = tempfile.TemporaryDirectory()
        cleanup = temporary.cleanup
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )

        service = mocker.Mock()
        service.uninstall.side_effect = lambda _ctx: events.append("service-uninstall")
        service.status.side_effect = lambda _ctx: events.append("service-status") or "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)

        def inventory(_proxy_script):
            events.append("inventory")
            return [123] if events.count("inventory") == 1 else []

        try:
            mocker.patch.object(process, "pids_naming_executable", side_effect=inventory)
            captured = process.OwnedProcess(123, "/tmp/owned/proxy.py", 42.0)
            mocker.patch.object(
                process,
                "capture_executable",
                side_effect=lambda *_args, **_kwargs: events.append("capture") or captured,
            )
            mocker.patch.object(
                process,
                "terminate_owned_process",
                side_effect=lambda *_args, **_kwargs: events.append("terminate") or True,
            )
            mocker.patch.object(process, "owned_process_alive", return_value=False)
            mocker.patch.object(temporary, "cleanup", side_effect=lambda: events.append("cleanup"))
            self._cleanup_installed_fixture(temporary, ctx, {})
        finally:
            cleanup()

        assert events == [
            "service-uninstall",
            "service-status",
            "inventory",
            "capture",
            "terminate",
            "cleanup",
        ]

    def test_fixture_cleanup_retries_a_transient_native_payload_lock(self, *, mocker) -> None:
        temporary = mocker.Mock()
        temporary.cleanup.side_effect = [PermissionError("mapped module"), None]
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )
        service = mocker.Mock()
        service.status.return_value = "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)
        mocker.patch.object(process, "pids_naming_executable", return_value=[])

        self._cleanup_installed_fixture(temporary, ctx, {})

        assert temporary.cleanup.call_count == 2

    def test_fixture_cleanup_uses_the_observed_successor_when_inventory_misses_it(
        self, *, mocker
    ) -> None:
        temporary = mocker.Mock()
        owned = process.OwnedProcess(123, "/tmp/owned/proxy.py", 42.0)
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )
        service = mocker.Mock()
        service.status.return_value = "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)
        mocker.patch.object(process, "pids_naming_executable", return_value=[])
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        alive = mocker.patch.object(process, "owned_process_alive", return_value=False)

        self._cleanup_installed_fixture(temporary, ctx, {123: owned})

        terminate.assert_called_once_with(owned)
        alive.assert_called_once_with(owned)
        temporary.cleanup.assert_called_once_with()

    def test_owned_child_cleanup_is_bound_to_its_temporary_proxy_path(self, *, mocker) -> None:
        proxy_script = "/tmp/owned/.codex/responses-proxy/proxy.py"
        owns_pid = mocker.patch.object(process, "pid_names_executable", return_value=True)
        terminate = mocker.patch.object(process, "terminate_executable", return_value=True)
        terminate_owned_proxy(123, proxy_script)

        owns_pid.assert_called_once()
        terminate.assert_called_once()

    def test_owned_child_cleanup_reports_an_unconfirmed_exit(self, *, mocker) -> None:
        proxy_script = "/tmp/owned/.codex/responses-proxy/proxy.py"
        command = f"python {proxy_script} --handoff-child"
        mocker.patch.object(process, "pid_names_executable", return_value=True)
        mocker.patch.object(process, "process_command", return_value=command)
        mocker.patch.object(process, "terminate_executable", return_value=False)
        with pytest.raises(RuntimeError, match="owned proxy child 123 did not terminate"):
            terminate_owned_proxy(123, proxy_script)

    def _post_responses(self, port, *, timeout=15):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=b'{"stream": false, "input": []}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return response.read()

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
