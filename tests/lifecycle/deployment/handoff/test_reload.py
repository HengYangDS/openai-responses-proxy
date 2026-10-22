"""Installed reload wiring for the rolling-handoff protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health


class TestControllerHandoffWiring(HandoffFixture):
    def test_reload_uses_handoff_without_terminating_the_old_pid_when_supported(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        native = mocker.patch.object(control, "adapter").return_value
        native.configured_executable.return_value = ctx.executable
        mocker.patch.object(control, "read_runtime", return_value=idle_runtime())
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection, "verify_payload_manifest", return_value=(True, "ok")
        )
        mocker.patch.object(handoff, "expected_metadata", return_value=expected_metadata())
        request_handoff = mocker.patch.object(
            handoff,
            "request",
            return_value={"old_pid": 1, "child_pid": 2, "release": "1.0.25"},
        )
        mocker.patch.object(
            handoff,
            "capture_source_listener",
            return_value=process.OwnedProcess(999, ctx.executable, 0.5),
        )
        terminate = mocker.patch.object(process, "terminate_pid")
        result = control.reload(ctx, timeout_seconds=5)
        request_handoff.assert_called_once()
        terminate.assert_not_called()
        assert result == {"state": "reloaded", "old_pid": 1, "new_pid": 2}
        assert request_handoff.call_args.kwargs["lease_seconds"] == ctx.upstream_timeout

    def test_reload_rejects_handoff_when_installed_payload_integrity_fails(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        mocker.patch.object(control, "read_runtime", return_value={"handoff_protocol_version": 2})
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection,
            "verify_payload_manifest",
            return_value=(False, "hash mismatch"),
        )
        request_handoff = mocker.patch.object(handoff, "request")
        with pytest.raises(errors.InstallError, match="integrity"):
            control.reload(ctx, timeout_seconds=5)
        request_handoff.assert_not_called()

    def test_reload_recovers_a_finalized_result_after_controller_failure(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        native = mocker.patch.object(control, "adapter").return_value
        native.configured_executable.return_value = ctx.executable
        expected = expected_metadata()
        old = idle_runtime()
        finalized = matching_health(1000, expected, pid=1000, handoff_state="finalized")
        mocker.patch.object(control, "read_runtime", return_value=old)
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection, "verify_payload_manifest", return_value=(True, "ok")
        )
        mocker.patch.object(handoff, "expected_metadata", return_value=expected)
        mocker.patch.object(handoff, "request", side_effect=KeyboardInterrupt())
        mocker.patch.object(
            handoff,
            "capture_source_listener",
            return_value=process.OwnedProcess(999, ctx.executable, 0.5),
        )
        mocker.patch.object(
            handoff,
            "resolve_after_controller_failure",
            return_value=("finalized", finalized),
        )
        result = control.reload(ctx, timeout_seconds=5)
        assert result["new_pid"] == 1000
        assert result["recovered_after_controller_failure"]
