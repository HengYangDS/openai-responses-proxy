"""Fresh deployment completion and bounded compensation."""

from __future__ import annotations

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import OrderedServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_fresh_install_commits_and_finalizes_after_runtime_proof(self, *, mocker) -> None:
        events: list[object] = []
        payload = FakeTransaction(self.ctx, events=events)
        service = OrderedServiceAdapter(events, mocker=mocker)
        runtime = self.successor(pid=123)
        mocker.patch.object(process, "listener_pids", return_value=[])
        mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)

        result = self.deploy(payload, None, adapter=service, mocker=mocker)

        assert result == {"state": "installed", "runtime": runtime}
        assert payload.events == [
            "commit",
            "activate",
            "service-install",
            ("finalize", runtime),
        ]
        service.install_mock.assert_called_once_with(payload.context)

    def test_fresh_failure_rolls_back(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("service failed"), mocker=mocker)
        mocker.patch.object(process, "listener_pids", return_value=[])
        with pytest.raises(errors.InstallError, match="service failed"):
            self.deploy(payload, None, adapter=service, mocker=mocker)
        assert payload.events == ["commit", "activate", "rollback"]

    def test_service_admission_rejection_rolls_back_without_native_cleanup(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(
            failure=errors.NativeServiceUnavailableError("systemd user manager is unavailable"),
            mocker=mocker,
        )
        service.uninstall_mock.side_effect = errors.InstallError("user bus is unavailable")
        terminate = mocker.spy(service, "terminate_runtime")
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(errors.NativeServiceUnavailableError, match="systemd user manager"):
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert payload.events == ["commit", "activate", "rollback"]
        service.uninstall_mock.assert_not_called()
        terminate.assert_not_called()

    @pytest.mark.parametrize("stage", ["runtime-cleanup", "payload-rollback"])
    @pytest.mark.parametrize("public", [False, True])
    def test_fresh_failure_retains_initial_and_compensation_causes(
        self, stage: str, public: bool, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        initial = (
            errors.InstallError("scheduled successor is unproved")
            if public
            else OSError("private initial path and token")
        )
        compensation = (
            errors.InstallError("generation removal failed (OS error 32)")
            if public
            else OSError("private compensation path and token")
        )
        service = FakeServiceAdapter(failure=initial, mocker=mocker)
        mocker.patch.object(process, "listener_pids", return_value=[])
        if stage == "runtime-cleanup":
            service.uninstall_mock.side_effect = compensation
        else:
            mocker.patch.object(payload, "rollback", side_effect=compensation)

        with pytest.raises(errors.InstallError) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        message = str(raised.value)
        assert "fresh installation failed" in message
        assert (
            "scheduled successor is unproved" if public else "operating-system operation failed"
        ) in message
        assert (
            "generation removal failed (OS error 32)"
            if public
            else "operating-system operation failed"
        ) in message
        assert "private" not in message
        assert (
            "payload rollback failed" if stage == "payload-rollback" else "cleanup failure"
        ) in message
        if stage == "runtime-cleanup":
            assert isinstance(raised.value, apply.UnknownDeploymentOutcome)
            assert payload.events == ["commit", "activate", ("preserve", message)]
        else:
            assert payload.events == ["commit", "activate"]

    def test_fresh_failure_does_not_claim_a_failed_recovery_record_was_preserved(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("startup failed"), mocker=mocker)
        service.uninstall_mock.side_effect = errors.InstallError("native cleanup failed")
        mocker.patch.object(
            payload,
            "preserve_for_recovery",
            side_effect=errors.InstallError("journal ownership changed"),
        )
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(apply.UnknownDeploymentOutcome) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        message = str(raised.value)
        assert "fresh installation failed: startup failed" in message
        assert "cleanup failure: native cleanup failed" in message
        assert "recovery record failed: journal ownership changed" in message
        assert "transaction preserved" not in message
        assert payload.events == ["commit", "activate"]

    def test_service_admission_failure_retains_failed_payload_rollback(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(
            failure=errors.NativeServiceUnavailableError("user service is unavailable"),
            mocker=mocker,
        )
        mocker.patch.object(
            payload,
            "rollback",
            side_effect=errors.InstallError("generation removal failed (OS error 5)"),
        )
        mocker.patch.object(process, "listener_pids", return_value=[])
        terminate = mocker.spy(service, "terminate_runtime")

        with pytest.raises(errors.NativeServiceUnavailableError) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert str(raised.value) == (
            "fresh installation failed: user service is unavailable; "
            "payload rollback failed: generation removal failed (OS error 5)"
        )
        assert payload.events == ["commit", "activate"]
        service.uninstall_mock.assert_not_called()
        terminate.assert_not_called()

    def test_fresh_failure_preserves_unconfirmed_candidate_cleanup(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("service failed"), mocker=mocker)
        service.uninstall_mock.side_effect = errors.InstallError("cleanup failed")
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(apply.UnknownDeploymentOutcome, match="cleanup is unconfirmed"):
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                "fresh installation failed: service failed; "
                "candidate runtime cleanup is unconfirmed; cleanup failure: cleanup failed; "
                "transaction preserved for recovery",
            ),
        ]
