"""Successor handoff, supervisor rebind and recovery authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import OrderedServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_current_upgrade_rebinds_control_after_successor_handoff(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        capture = mocker.patch.object(process, "capture_executable", return_value=source)
        request = mocker.patch.object(
            apply,
            "request_handoff",
            return_value=runtime,
        )

        result = self.deploy(payload, current, adapter=service, mocker=mocker)

        assert result == {"state": "upgraded", "runtime": runtime}
        assert payload.events == ["commit", "activate", ("finalize", runtime)]
        capture.assert_called_once_with(
            111,
            self.ctx.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
        request.assert_called_once_with(
            payload.context,
            payload.expected,
            current=current,
            source_listener=source,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
        )
        service.uninstall_mock.assert_not_called()
        service.install_mock.assert_called_once_with(payload.context)

    def test_candidate_processes_exit_before_payload_rollback(self, *, mocker) -> None:
        events: list[object] = []
        payload = FakeTransaction(self.ctx, events=events)
        current = self.current_runtime()
        service = OrderedServiceAdapter(events, mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=errors.InstallError("successor unavailable"),
        )
        mocker.patch.object(apply.handoff, "resume_responses", return_value=True)

        def terminate(ctx, *, timeout_seconds):
            del timeout_seconds
            events.extend((("candidate-exit", 222), ("candidate-exit", 333)))
            service.running_contexts.discard(ctx.executable)
            return 2

        service.terminate_runtime = mocker.Mock(side_effect=terminate)

        with pytest.raises(errors.InstallError, match="successor unavailable"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert events == [
            "commit",
            "activate",
            "rollback",
        ]
        service.uninstall_mock.assert_not_called()
        service.install_mock.assert_not_called()
        service.terminate_runtime.assert_not_called()

    def test_current_upgrade_accepts_an_equivalent_supervisor_path(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        executable = Path(self.ctx.executable)
        candidate_executable = Path(payload.context.executable)
        service.configured_executable = mocker.Mock(
            side_effect=[
                str(executable.parent / ".." / executable.parent.name / executable.name),
                str(
                    candidate_executable.parent
                    / ".."
                    / candidate_executable.parent.name
                    / candidate_executable.name
                ),
            ]
        )
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)

        assert self.deploy(payload, current, adapter=service, mocker=mocker) == {
            "state": "upgraded",
            "runtime": runtime,
        }
        request.assert_called_once()

    def test_current_upgrade_refuses_an_unproved_supervisor_rebind(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        service.configured_executable = mocker.Mock(side_effect=[self.ctx.executable, None])
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        runtime = self.successor()
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="supervisor rebind failed",
        ):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                (
                    "successor committed but native supervisor rebind failed; "
                    "failure: native supervisor did not bind the lifecycle "
                    "control executable"
                ),
            ),
        ]
        service.install_mock.assert_called_once_with(payload.context)
        service.uninstall_mock.assert_not_called()
        request.assert_called_once()

    def test_supervisor_install_failure_preserves_the_committed_successor(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        service.install_mock.side_effect = errors.InstallError("service failed")
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        runtime = self.successor()
        mocker.patch.object(apply, "request_handoff", return_value=runtime)

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="supervisor rebind failed",
        ):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                "successor committed but native supervisor rebind failed; failure: service failed",
            ),
        ]
        service.install_mock.assert_called_once_with(payload.context)
        service.uninstall_mock.assert_not_called()

    def test_upgrade_failure_rolls_back_or_preserves_unknown_outcome(self, *, mocker) -> None:
        current = self.current_runtime()
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])

        rolled_back = FakeTransaction(self.ctx)
        rollback_service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(
            apply, "request_handoff", side_effect=errors.InstallError("handoff failed")
        )
        resume = mocker.patch.object(apply.handoff, "resume_responses")
        with pytest.raises(errors.InstallError, match="handoff failed"):
            self.deploy(rolled_back, current, adapter=rollback_service, mocker=mocker)
        assert rolled_back.events == ["commit", "activate", "rollback"]
        resume.assert_called_once_with(
            self.ctx,
            source_listener=process.OwnedProcess(111, self.ctx.executable, 1.0),
            source_runtime=current,
            runtime_reader=mocker.ANY,
        )
        rollback_service.install_mock.assert_not_called()

        unknown = FakeTransaction(self.ctx)
        preserved_service = FakeServiceAdapter(mocker=mocker)
        resume.reset_mock()
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.UnknownDeploymentOutcome("outcome unknown"),
        )
        with pytest.raises(apply.UnknownDeploymentOutcome, match="outcome unknown"):
            self.deploy(unknown, current, adapter=preserved_service, mocker=mocker)
        assert unknown.events == ["commit", "activate", ("preserve", "outcome unknown")]
        resume.assert_not_called()
        preserved_service.install_mock.assert_not_called()

    def test_upgrade_preserves_a_predecessor_that_cannot_reopen_admission(self, *, mocker) -> None:
        """A restored supervisor is not a successful rollback until admission reopens."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=errors.InstallError("successor unavailable"),
        )
        mocker.patch.object(
            apply.handoff,
            "resume_responses",
            side_effect=errors.InstallError("drain release is unavailable"),
        )

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="could not restore predecessor admission",
        ) as raised:
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert str(raised.value) == (
            "native supervisor rollback could not restore predecessor admission; "
            "upgrade failure: successor unavailable; "
            "admission recovery failure: drain release is unavailable"
        )
        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                str(raised.value),
            ),
        ]
