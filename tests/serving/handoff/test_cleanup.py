"""Source-only contracts for exact subprocess fixture cleanup."""

from __future__ import annotations

import tempfile

import pytest

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from tests.serving.handoff.fixtures import terminate_owned_proxy
from tests.serving.handoff.subprocess.fixtures import SubprocessFixture


class TestHandoffCleanup(SubprocessFixture):
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
