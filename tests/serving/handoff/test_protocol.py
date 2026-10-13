"""Rolling-handoff wire protocol, transition, and platform contracts."""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving.handoff import protocol as handoff_protocol_module
from tests.serving.handoff import fixtures as handoff_fixtures
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import http_json
from tests.serving.handoff.fixtures import runtime_state_module

ROOT = Path(__file__).resolve().parents[3]


class TestProtocolContract:
    """The wire-level contract every other component below depends on."""

    def setup_method(self):
        self.p = entrypoint_module
        runtime_state_module.reset_for_test()
        handoff_module.reset_session_to_idle()

    def test_parent_and_controller_declare_protocol_version_two(self):
        assert (
            handoff_module.HANDOFF_PROTOCOL_VERSION,
            handoff.HANDOFF_PROTOCOL_VERSION,
        ) == (2, 2)

    def test_runtime_status_contains_full_handoff_health_shape(self):
        status = self.p.runtime_status()
        required = (
            "pid",
            "handoff_protocol_version",
            "handoff_transaction_id",
            "handoff_state",
            "handoff_capabilities",
            "release",
            "serving_payload_sha256",
            "release_receipt_sha256",
            "payload_manifest_sha256",
            "accepting",
            "draining",
        )
        assert set(required).difference(status) == set()
        assert status["handoff_protocol_version"] == 2
        assert status["handoff_capabilities"] == [
            handoff_module.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_module.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ]
        assert status["pid"] == os.getpid()

    def test_runtime_status_reports_an_accepting_idle_state(self):
        status = self.p.runtime_status()
        assert (
            status["accepting"],
            status["draining"],
            status["handoff_transaction_id"],
        ) == (
            True,
            False,
            None,
        )
        assert status["handoff_state"] in (None, "idle")

    def test_a_prepared_or_committing_handoff_keeps_request_admission_open(
        self,
    ):
        def assert_accepting(state):
            handoff_module._HANDOFF_SESSION["state"] = state
            try:
                status = self.p.runtime_status()
                assert (status["draining"], status["accepting"]) == (False, True)
            finally:
                handoff_module.reset_session_to_idle()

        for state in ("preparing", "ready", "committing", "serving", "finalizing"):
            assert_accepting(state)

    def test_healthz_over_real_loopback_http_exposes_the_same_shape(self):
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "https://gateway.example/v1")}
        )
        proxy = self.p.create_server(("127.0.0.1", 0), providers=providers)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            status_code, payload = http_json(proxy.server_address[1], "/healthz")
            assert status_code == 200
            required = (
                "pid",
                "handoff_protocol_version",
                "handoff_transaction_id",
                "handoff_state",
                "handoff_capabilities",
                "payload_manifest_sha256",
                "accepting",
            )
            assert set(required).difference(payload) == set()
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)


class TestHandoffTransitionValidation:
    def test_transition_table_matches_the_documented_protocol(self):
        allowed = frozenset(
            (
                ("idle", "preparing"),
                ("preparing", "ready"),
                ("ready", "committing"),
                ("committing", "serving"),
                ("serving", "finalizing"),
                ("finalizing", "finalized"),
                ("finalized", "idle"),
                ("aborting", "rolled_back"),
                ("rolled_back", "idle"),
                *tuple(
                    (state, "aborting")
                    for state in (
                        "preparing",
                        "ready",
                        "committing",
                        "serving",
                        "finalizing",
                    )
                ),
            )
        )
        states = {state for transition in allowed for state in transition}
        results = {
            (current, target): handoff_module.validate_transition(current, target)
            for current in states
            for target in states
        }
        assert {transition for transition, valid in results.items() if valid} == allowed


class TestHandoffPlatformHelpers:
    def setup_method(self):
        self.p = handoff_protocol_module

    def test_platform_popen_kwargs_are_minimal_and_inherit_only_the_listener(self):
        posix = self.p.popen_kwargs(37, is_windows=False)
        windows = self.p.popen_kwargs(None, is_windows=True)
        assert posix["pass_fds"] == (37,)
        assert "pass_fds" not in windows
        projected = tuple(
            (kwargs["close_fds"], kwargs["stdin"], kwargs["stdout"]) for kwargs in (posix, windows)
        )
        assert projected == ((True, subprocess.PIPE, subprocess.PIPE),) * 2
        with pytest.raises(self.p.HandoffError, match="requires a listener fd"):
            self.p.popen_kwargs(None, is_windows=False)

    def test_child_constructor_requires_both_control_pipes(self, *, mocker):
        def reject_pipes(pipes):
            process = mocker.Mock(stdin=pipes[0], stdout=pipes[1])
            with pytest.raises(self.p.HandoffError, match="pipes are unavailable"):
                self.p.HandoffChild(process)

        for pipes in ((None, mocker.Mock()), (mocker.Mock(), None)):
            reject_pipes(pipes)

        child = self.p.HandoffChild(mocker.Mock(stdin=io.BytesIO(), stdout=io.BytesIO()))
        with pytest.raises(self.p.HandoffError, match="runtime has not started"):
            _ = child.runtime_pid

    def test_child_message_writer_rejects_oversized_messages_and_broken_pipes(self, *, mocker):
        process = mocker.Mock(stdin=mocker.Mock(), stdout=mocker.Mock())
        child = self.p.HandoffChild(process)
        with pytest.raises(self.p.HandoffError, match="exceeds the control limit"):
            child.send_message({"value": "x" * self.p.HANDOFF_CONTROL_MAX_BYTES})
        process.stdin.write.side_effect = BrokenPipeError
        with pytest.raises(self.p.HandoffError, match="pipe write failed"):
            child.send_message({"type": "prepare"})

    def test_child_reader_projects_timeout_and_malformed_messages(self, *, mocker):
        def reject(case):
            raw, message = case
            process = mocker.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(raw))
            child = self.p.HandoffChild(process)
            with pytest.raises(self.p.HandoffError, match=message):
                child.recv_message(0.1)

        cases = (
            (b"", "control pipe closed"),
            (
                b"x" * (self.p.HANDOFF_CONTROL_MAX_BYTES + 1),
                "exceeds the control limit",
            ),
            (b"{\n", "invalid JSON"),
            (b"[]\n", "must be an object"),
        )
        for case in cases:
            reject(case)

        process = mocker.Mock(stdin=io.BytesIO(), stdout=mocker.Mock())
        child = self.p.HandoffChild(process)
        child._reader_started = True
        with pytest.raises(self.p.HandoffError, match="response timed out"):
            child.recv_message(0)

        child = self.p.HandoffChild(
            mocker.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"type":"ready"}\n'))
        )
        assert child.recv_message(1) == {"type": "ready"}
        child._start_reader()
        assert child._reader_started

        broken = mocker.Mock()
        broken.readline.side_effect = OSError
        child = self.p.HandoffChild(mocker.Mock(stdin=io.BytesIO(), stdout=broken))
        with pytest.raises(self.p.HandoffError, match="pipe read failed"):
            child.recv_message(0.1)

        child = self.p.HandoffChild(
            mocker.Mock(
                stdin=io.BytesIO(),
                stdout=io.BytesIO(b'{"type":"started","pid":false}\n'),
            )
        )
        with pytest.raises(self.p.HandoffError, match="STARTED identity mismatch"):
            child.await_runtime(1)

    def test_bounded_child_shutdown_handles_already_exited_success_and_timeout(self, *, mocker):
        def verify(case):
            method, poll_result, poll_effect, wait, expected = case
            process = mocker.Mock(stdin=mocker.Mock(), stdout=mocker.Mock())
            process.poll.return_value = poll_result
            process.poll.side_effect = poll_effect
            process.wait.side_effect = wait
            assert getattr(self.p.HandoffChild(process), method)(0) is expected

        outcomes = (
            (0, None, None, True),
            (None, None, None, True),
            (None, [None, None], subprocess.TimeoutExpired("child", 0.01), False),
        )
        cases = (
            (method, poll_result, poll_effect, wait, expected)
            for method in ("terminate_bounded", "kill_bounded")
            for poll_result, poll_effect, wait, expected in outcomes
        )
        for case in cases:
            verify(case)

    def test_spawn_projects_only_the_platform_listener_capability(self, subtests, *, mocker):
        expected = expected_metadata(transaction_id="txn-platform", release_receipt_sha256="e" * 64)
        for is_windows, pid in ((False, 4242), (True, 5150)):
            with subtests.test(is_windows=is_windows):
                process = mocker.Mock(
                    pid=pid,
                    stdin=mocker.Mock(),
                    stdout=io.BytesIO(json.dumps({"type": "started", "pid": pid}).encode() + b"\n"),
                )
                listener = mocker.Mock()
                listener.fileno.return_value = 37
                listener.share.return_value = b"opaque-share-bytes"
                popen = mocker.patch("subprocess.Popen", return_value=process)
                child = self.p.spawn_child(
                    listener,
                    expected,
                    entrypoint_module._handoff_context(),
                    is_windows=is_windows,
                )
                assert child.process is process
                args, kwargs = popen.call_args
                assert args[0] == [
                    str(entrypoint_module._handoff_context().executable),
                    "--internal-handoff-child",
                ]
                environment = kwargs["env"]
                assert "PYTHONPATH" not in environment
                assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
                assert environment[self.p.runtime_config.HOME_ENV] == str(
                    entrypoint_module._handoff_context().executable.parent.parent
                )
                assert environment[self.p.runtime_config.STATE_HOME_ENV]
                written = b"".join(call.args[0] for call in process.stdin.write.call_args_list)
                if is_windows:
                    assert "pass_fds" not in kwargs
                    assert b"opaque-share-bytes" not in str(args).encode()
                    assert "opaque-share-bytes" not in json.dumps(kwargs.get("env") or {})
                    listener.share.assert_called_once_with(pid)
                    assert (
                        base64.b64decode(json.loads(written.splitlines()[0])["listener_share_b64"])
                        == b"opaque-share-bytes"
                    )
                    continue
                assert kwargs["pass_fds"] == (37,)
                assert b"listener_share_b64" not in written

    def test_spawn_binds_protocol_identity_to_the_runtime_not_the_launcher(
        self, subtests, *, mocker
    ):
        expected = expected_metadata(transaction_id="txn-runtime-pid")
        launcher_pid = 4242
        runtime_pid = 5150
        for is_windows in (False, True):
            with subtests.test(is_windows=is_windows):
                process = mocker.Mock(
                    pid=launcher_pid,
                    stdin=io.BytesIO(),
                    stdout=io.BytesIO(
                        json.dumps({"type": "started", "pid": runtime_pid}).encode() + b"\n"
                    ),
                )
                listener = mocker.Mock()
                listener.fileno.return_value = 37
                listener.share.return_value = b"opaque-share-bytes"
                mocker.patch("subprocess.Popen", return_value=process)
                child = self.p.spawn_child(
                    listener,
                    expected,
                    entrypoint_module._handoff_context(),
                    is_windows=is_windows,
                )

                assert child.process.pid == launcher_pid
                assert child.runtime_pid == runtime_pid
                if is_windows:
                    listener.share.assert_called_once_with(runtime_pid)

    def test_windows_share_failure_terminates_the_owned_child(self, *, mocker):
        fake_process = mocker.Mock(
            pid=5150,
            stdin=mocker.Mock(),
            stdout=io.BytesIO(b'{"type":"started","pid":5150}\n'),
        )
        listener = mocker.Mock()
        listener.share.side_effect = OSError("sharing failed")
        mocker.patch("subprocess.Popen", return_value=fake_process)
        terminate = mocker.patch.object(self.p.HandoffChild, "terminate_bounded", return_value=True)
        with pytest.raises(self.p.HandoffError, match="listener sharing failed"):
            self.p.spawn_child(
                listener,
                expected_metadata(transaction_id="txn-platform", release_receipt_sha256="e" * 64),
                entrypoint_module._handoff_context(),
                is_windows=True,
            )
        terminate.assert_called_once()

    def test_spawn_write_failure_escalates_from_terminate_to_kill(self, *, mocker):
        fake_process = mocker.Mock(
            pid=4242,
            stdin=mocker.Mock(),
            stdout=io.BytesIO(b'{"type":"started","pid":4242}\n'),
        )
        listener = mocker.Mock()
        listener.fileno.return_value = 37
        mocker.patch("subprocess.Popen", return_value=fake_process)
        mocker.patch.object(self.p.HandoffChild, "send_message", side_effect=BrokenPipeError)
        terminate = mocker.patch.object(
            self.p.HandoffChild, "terminate_bounded", return_value=False
        )
        kill = mocker.patch.object(self.p.HandoffChild, "kill_bounded", return_value=True)
        with pytest.raises(BrokenPipeError):
            self.p.spawn_child(
                listener,
                expected_metadata(transaction_id="txn-platform", release_receipt_sha256="e" * 64),
                entrypoint_module._handoff_context(),
                is_windows=False,
            )
        terminate.assert_called_once()
        kill.assert_called_once()

    def test_listener_reconstruction_uses_the_platform_specific_projection(self, *, mocker):
        fake_socket = mocker.Mock()
        share = base64.b64encode(b"share-bytes").decode("ascii")
        ctor = mocker.patch.object(socket, "fromshare", create=True, return_value=fake_socket)
        assert self.p.listener_from_prepare({"listener_share_b64": share}) is fake_socket
        ctor.assert_called_once_with(b"share-bytes")
        ctor = mocker.patch.object(socket, "socket", return_value=fake_socket)
        assert self.p.listener_from_prepare({"listener_fd": 37}) is fake_socket
        ctor.assert_called_once_with(fileno=37)

    def test_listener_reconstruction_rejects_invalid_platform_payloads(self):
        def reject(message):
            with pytest.raises(self.p.HandoffError):
                self.p.listener_from_prepare(message)

        invalid = (
            {"listener_share_b64": 7},
            {"listener_share_b64": "x" * (self.p.HANDOFF_CONTROL_MAX_BYTES + 1)},
            {"listener_share_b64": "not base64!"},
            {"listener_fd": -1},
            {"listener_fd": "37"},
        )
        for message in invalid:
            reject(message)

    def test_control_message_helpers_reject_invalid_framing_and_round_trip(self):
        target = io.BytesIO()
        self.p.write_control_message(target, {"type": "commit"})
        assert self.p.read_control_message(io.BytesIO(target.getvalue())) == {"type": "commit"}

        def reject(case):
            raw, error = case
            with pytest.raises(error):
                self.p.read_control_message(io.BytesIO(raw))

        invalid = (
            (b"", EOFError),
            (b"x" * (self.p.HANDOFF_CONTROL_MAX_BYTES + 1), self.p.HandoffError),
            (b"{\n", self.p.HandoffError),
            (b"[]\n", self.p.HandoffError),
        )
        for case in invalid:
            reject(case)
        with pytest.raises(self.p.HandoffError):
            self.p.write_control_message(
                io.BytesIO(), {"value": "x" * self.p.HANDOFF_CONTROL_MAX_BYTES}
            )

    def test_child_protocol_accepts_finalize_and_rejects_invalid_commands(
        self, subtests, *, mocker
    ):
        context = entrypoint_module._handoff_context()
        prepare = {
            "type": "prepare",
            "protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
            "transaction_id": "txn-child",
            "release": entrypoint_module.release_version(),
            "serving_payload_sha256": entrypoint_module.loaded_serving_payload_sha256(),
            "release_receipt_sha256": entrypoint_module.release_receipt_sha256(),
            "manifest_sha256": context.payload_manifest_sha256(),
            "listener_fd": 37,
        }

        def run(*commands, valid_prepare=True, server=None):
            handoff_module.reset_session_to_idle()
            first = prepare if valid_prepare else {**prepare, "protocol_version": 1}
            raw = b"".join(
                json.dumps(message, separators=(",", ":")).encode() + b"\n"
                for message in (first, *commands)
            )
            fake_stdin = mocker.Mock(buffer=io.BytesIO(raw))
            fake_stdout = mocker.Mock(buffer=io.BytesIO())
            fake_server = server or mocker.Mock()
            fake_server.server_address = ("127.0.0.1", 8792)
            object.__setattr__(context, "server_factory", lambda _listener: fake_server)
            mocker.patch.object(sys, "stdin", fake_stdin)
            mocker.patch.object(sys, "stdout", fake_stdout)
            mocker.patch.object(handoff_module, "listener_from_prepare", return_value=mocker.Mock())
            mocker.patch.object(handoff_module, "serve_with_resume")
            probe = mocker.patch.object(
                handoff_module,
                "probe_health",
                return_value={"pid": os.getpid()},
            )
            thread_cls = mocker.patch("threading.Thread")
            result = handoff_module.run_child(context)
            assert thread_cls.return_value.start.call_count == int({"type": "commit"} in commands)
            if {"type": "commit"} in commands:
                probe.assert_called_once_with(
                    8792,
                    timeout_seconds=handoff_module.HANDOFF_READY_TIMEOUT_SECONDS,
                    expected={
                        "pid": os.getpid(),
                        "handoff_protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
                        "handoff_transaction_id": prepare["transaction_id"],
                        "release": prepare["release"],
                        "serving_payload_sha256": prepare["serving_payload_sha256"],
                        "release_receipt_sha256": prepare["release_receipt_sha256"],
                        "payload_manifest_sha256": prepare["manifest_sha256"],
                        "handoff_state": "serving",
                        "accepting": True,
                        "draining": False,
                    },
                )
            else:
                probe.assert_not_called()
            return (
                result,
                fake_server,
                [json.loads(line) for line in fake_stdout.buffer.getvalue().splitlines()],
            )

        result, server, messages = run({"type": "commit"}, {"type": "finalize"})
        assert result == 0
        assert [message["type"] for message in messages] == [
            "started",
            "ready",
            "serving",
            "finalized",
        ]
        server.server_close.assert_not_called()

        for commands, valid_prepare in (
            ((), False),
            (({"type": "unexpected"},), True),
            (({"type": "commit"}, {"type": "abort"}), True),
            (({"type": "commit"}, {"type": "unexpected"}), True),
        ):
            with subtests.test(commands=commands, valid_prepare=valid_prepare):
                result, server, _ = run(*commands, valid_prepare=valid_prepare)
                assert result == 1
                assert server.server_close.call_count == int(valid_prepare)

        failed = mocker.Mock()
        failed.shutdown.side_effect = OSError
        failed.server_close.side_effect = OSError
        result, failed, _ = run({"type": "commit"}, {"type": "abort"}, server=failed)
        assert result == 1
        failed.shutdown.assert_called_once()
        failed.server_close.assert_called_once()


def test_initial_proxy_spawn_avoids_multithreaded_posix_fork(tmp_path: Path, mocker) -> None:
    """A mocked launch contract must not build or prewarm a real native payload."""
    payload = tmp_path / "generation"
    payload.mkdir()
    upstream = "http://127.0.0.1:43123"
    (payload / inventory.PROVIDER_MANIFEST).write_text(
        f'version = 1\n\n[providers.dmxapi]\nbase_url = "{upstream}"\npolicy = "dmxapi"\n',
        encoding="utf-8",
    )
    ctx = runtime_context.RuntimeContext(
        install_dir=str(tmp_path / "payload"),
        executable=str(payload / "bin" / "proxy"),
        command=str(tmp_path / "command"),
        log_dir=str(tmp_path / "state"),
        user_home=str(tmp_path),
        port=43124,
    )
    build = mocker.patch.object(handoff_fixtures, "write_installed_payload")
    mocker.patch.dict(handoff_fixtures.os.environ, {"OPENAI_RESPONSES_PROXY_EXECUTABLE": "foreign"})
    child = mocker.Mock()
    child.poll.return_value = None
    mocker.patch.object(handoff_fixtures, "proxy_is_up", return_value=True)
    popen = mocker.patch.object(handoff_fixtures.subprocess, "Popen", return_value=child)
    try:
        started = handoff_fixtures.start_real_proxy(
            ctx, upstream_url=upstream, log_path=tmp_path / "proxy.log"
        )
    finally:
        mocker.stop(popen)

    assert started is child
    build.assert_not_called()
    assert popen.call_args.kwargs["close_fds"] is True
    environment = popen.call_args.kwargs["env"]
    assert environment["OPENAI_RESPONSES_PROXY_HOME"] == ctx.install_dir
    assert environment["OPENAI_RESPONSES_PROXY_STATE_HOME"] == str(tmp_path / "state")
    assert environment["OPENAI_RESPONSES_PROXY_EXECUTABLE"] == ctx.executable
