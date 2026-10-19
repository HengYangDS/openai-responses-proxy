"""Watchdog availability, spawn, retention, and loop contracts."""

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import watchdog
from tests.lifecycle.fixtures import assert_private_log_mode


def load_watchdog():
    """Load a fresh watchdog module so tests cannot share mutable globals."""
    return importlib.reload(watchdog)


class TestWatchdogEntrypoint:
    def test_installed_watchdog_module_imports_without_checkout_context(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import openai_responses_proxy.lifecycle.supervision.watchdog",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        assert completed.returncode == 0, completed.stderr


class TestWatchdogLogging:
    @pytest.mark.parametrize(
        ("condition", "event", "detail"),
        [
            ("spawned", "listener_spawned", "pid=42"),
            ("missing", "listener_spawn_refused", "reason=executable_missing"),
            ("denied", "listener_spawn_failed", "exception=PermissionError"),
            ("transaction", "listener_start_deferred", "reason=transaction_pending"),
            ("ownership", "listener_start_refused", "reason=invalid_runtime_ownership"),
        ],
    )
    def test_watchdog_events_identify_actions_without_private_context(
        self, condition, event, detail, tmp_path, *, mocker
    ):
        watchdog = load_watchdog()
        executable = tmp_path / "private-installation" / "openai-responses-proxy"
        executable.parent.mkdir()
        if condition != "missing":
            executable.write_text("native fixture\n", encoding="utf-8")
        log_path = tmp_path / "watchdog.log"
        mocker.patch.object(watchdog, "LOG_PATH", str(log_path))
        mocker.patch.object(watchdog, "EXECUTABLE", str(executable))
        mocker.patch.object(watchdog, "is_proxy_up", return_value=False)
        current = mocker.Mock(executable=str(executable))
        context = mocker.patch.object(watchdog.runtime_context, "create", return_value=current)
        mocker.patch.object(
            watchdog.records,
            "status",
            return_value={"state": "prepared"} if condition == "transaction" else None,
        )
        popen = mocker.patch.object(watchdog.subprocess, "Popen")
        popen.return_value.pid = 42
        if condition == "denied":
            popen.side_effect = PermissionError("private-credential-context")
        elif condition == "ownership":
            context.side_effect = errors.InstallError("private-credential-context")

        watchdog.run(max_iterations=1)

        lines = log_path.read_text(encoding="utf-8").splitlines()
        fields = [dict(field.split("=", 1) for field in line.split()[1:]) for line in lines]
        assert fields[0]["event"] == "watchdog_started"
        selected = next(item for item in fields if item["event"] == event)
        key, value = detail.split("=", 1)
        assert selected[key] == value
        assert "private-credential-context" not in "\n".join(lines)
        assert str(executable.parent) not in "\n".join(lines)
        if condition in {"transaction", "ownership", "missing"}:
            popen.assert_not_called()

    def test_watchdog_log_is_bounded_and_redacts_secrets(self, *, mocker):
        watchdog = load_watchdog()
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "watchdog.log"
            mocker.patch.object(watchdog, "LOG_PATH", str(log_path))
            mocker.patch.object(watchdog, "LOG_MAX_BYTES", 4096)
            mocker.patch.object(watchdog, "LOG_BACKUP_COUNT", 0)
            watchdog._log("authorization: Bearer super-secret-token encrypted=gAAAA_replay_secret")
            log_path.write_bytes(b"x" * 8192)
            watchdog._log("event=rotation_probe")
            text = log_path.read_text(encoding="utf-8")
            size = log_path.stat().st_size
            mode = log_path.stat().st_mode & 0o777
        assert "super-secret-token" not in text
        assert "gAAAA_replay_secret" not in text
        assert "log_retention_discarded_oversized_bytes=8192" in text
        assert size <= 4096
        assert_private_log_mode(mode)

    def test_watchdog_probe_and_spawn(self, *, mocker):
        watchdog = load_watchdog()
        connect = mocker.patch.object(watchdog.socket, "create_connection")
        connect.return_value.__enter__.return_value = None
        assert watchdog.is_proxy_up()
        connect.side_effect = OSError("offline")
        assert not watchdog.is_proxy_up()

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "openai-responses-proxy"
            executable.write_text("native fixture\n", encoding="utf-8")
            mocker.patch.object(watchdog, "EXECUTABLE", str(executable))
            mocker.patch.object(watchdog.os, "name", "posix")
            popen = mocker.patch.object(watchdog.subprocess, "Popen")
            mocker.patch.object(watchdog, "_log")
            popen.return_value.pid = 42
            assert watchdog.spawn_proxy(str(executable)) is popen.return_value
            assert popen.call_args.args[0] == [str(executable), watchdog.LISTENER_MODE]
            assert popen.call_args.kwargs["start_new_session"]
            environment = popen.call_args.kwargs["env"]
            assert environment[watchdog.runtime_config.HOME_ENV] == str(executable.parent.parent)
            assert environment[watchdog.runtime_config.STATE_HOME_ENV]
            assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
            assert "PYTHONPATH" not in environment
            mocker.patch.object(
                watchdog,
                "EXECUTABLE",
                str(executable.with_name("missing-native-executable")),
            )
            mocker.patch.object(watchdog, "_log")
            assert watchdog.spawn_proxy(watchdog.EXECUTABLE) is None
            mocker.patch.object(watchdog, "EXECUTABLE", str(executable))
            mocker.patch.object(watchdog.os, "name", "nt")
            popen = mocker.patch.object(watchdog.subprocess, "Popen")
            mocker.patch.object(watchdog, "_log")
            popen.return_value.pid = 43
            watchdog.spawn_proxy(str(executable))
            assert popen.call_args.kwargs["creationflags"] == watchdog._WINDOWS_DETACH_FLAGS
            windows_environment = popen.call_args.kwargs["env"]
            assert windows_environment[watchdog.runtime_config.HOME_ENV]
            assert windows_environment[watchdog.runtime_config.STATE_HOME_ENV]
            assert windows_environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
            assert "PYTHONPATH" not in windows_environment
            mocker.patch.object(watchdog, "EXECUTABLE", str(executable))
            mocker.patch.object(watchdog.subprocess, "Popen", side_effect=OSError("denied"))
            logged = mocker.patch.object(watchdog, "_log")
            assert watchdog.spawn_proxy(str(executable)) is None
            assert "OSError" in logged.call_args.args[0]

    def test_watchdog_reaps_exited_listener_children(self, *, mocker):
        watchdog = load_watchdog()
        running = mocker.Mock()
        running.poll.return_value = None
        exited = mocker.Mock()
        exited.poll.return_value = 0
        children = [running, exited]

        watchdog._reap_children(children)

        assert children == [running]
        running.poll.assert_called_once_with()
        exited.poll.assert_called_once_with()

    def test_watchdog_loop_is_bounded(self, *, mocker):
        watchdog = load_watchdog()
        current = mocker.Mock(executable="/installed/current")
        mocker.patch.object(watchdog.runtime_context, "create", return_value=current)
        transaction = mocker.patch.object(watchdog.records, "status", return_value=None)
        mocker.patch.object(watchdog, "is_proxy_up", return_value=False)
        spawn = mocker.patch.object(watchdog, "spawn_proxy")
        sleep = mocker.patch.object(watchdog.time, "sleep")
        mocker.patch.object(watchdog, "_log")
        watchdog.run(max_iterations=1)
        spawn.assert_called_once_with(current.executable)
        sleep.assert_not_called()

        transaction.return_value = {"state": "activated", "fresh": False}
        spawn.reset_mock()
        watchdog.run(max_iterations=1)
        spawn.assert_called_once_with(current.executable)

        transaction.return_value = {"state": "activated", "fresh": True}
        spawn.reset_mock()
        watchdog.run(max_iterations=1)
        spawn.assert_called_once_with(current.executable)

    def test_watchdog_never_crosses_invalid_transaction_evidence(self, *, mocker):
        watchdog = load_watchdog()
        mocker.patch.object(watchdog, "is_proxy_up", return_value=False)
        mocker.patch.object(watchdog.runtime_context, "create")
        mocker.patch.object(
            watchdog.records,
            "status",
            return_value={"state": "invalid", "detail": "journal is malformed"},
        )
        spawn = mocker.patch.object(watchdog, "spawn_proxy")
        mocker.patch.object(watchdog, "_log")

        watchdog.run(max_iterations=1)

        spawn.assert_not_called()
