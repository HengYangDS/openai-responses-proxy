"""Successor handoff, supervisor rebind and recovery authority."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_UNPROVED = "predecessor listener does not answer as captured, accepting and with no handoff begun"


class TestReleasedDeployment(ReleasedDeploymentFixture):
    @pytest.mark.parametrize(
        "capabilities",
        [
            [handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY],
            [handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY],
            [],
            None,
        ],
    )
    def test_upgrade_refuses_a_runtime_without_both_handoff_capabilities(
        self, capabilities: list[str] | None, *, mocker: MockerFixture
    ) -> None:
        """A verified runtime that cannot hand off is refused before any change."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(handoff_capabilities=capabilities)
        service = FakeServiceAdapter(mocker=mocker)
        request = mocker.patch.object(apply, "request_handoff")
        control_request = mocker.patch.object(
            apply.handoff.loopback, "open_request", side_effect=AssertionError("control request")
        )
        terminate = mocker.patch.object(process, "terminate_owned_process")

        with pytest.raises(errors.InstallError) as refused:
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert str(refused.value) == (
            "installed runtime is incompatible; remove it before installing this release"
        )
        assert payload.events == []
        service.install_mock.assert_not_called()
        service.uninstall_mock.assert_not_called()
        request.assert_not_called()
        control_request.assert_not_called()
        terminate.assert_not_called()

    def test_current_upgrade_rebinds_control_after_successor_handoff(
        self, *, mocker: MockerFixture
    ) -> None:
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

    def test_current_upgrade_accepts_an_equivalent_supervisor_path(
        self, *, mocker: MockerFixture
    ) -> None:
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

    def test_current_upgrade_refuses_an_unproved_supervisor_rebind(
        self, *, mocker: MockerFixture
    ) -> None:
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

    def test_supervisor_install_failure_preserves_the_committed_successor(
        self, *, mocker: MockerFixture
    ) -> None:
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

    def test_activation_failure_rolls_back_before_any_handoff(
        self, *, mocker: MockerFixture
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        mocker.patch.object(
            payload, "activate", side_effect=errors.InstallError("activation failed")
        )
        evidence = mocker.Mock(return_value=current)
        request = mocker.patch.object(apply, "request_handoff")

        with pytest.raises(errors.InstallError, match="activation failed"):
            self.deploy(payload, current, runtime_reader=evidence, mocker=mocker)

        assert payload.events == ["commit", "rollback"]
        evidence.assert_called_once_with(self.ctx)
        request.assert_not_called()

    def test_upgrade_failure_rolls_back_or_preserves_unknown_outcome(
        self, *, mocker: MockerFixture
    ) -> None:
        current = self.current_runtime()
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        control_request = mocker.patch.object(apply.handoff.loopback, "open_request")

        rolled_back = FakeTransaction(self.ctx)
        rollback_service = FakeServiceAdapter(mocker=mocker)
        terminate = mocker.patch.object(rollback_service, "terminate_runtime")
        # Only a request that the listener has finished with rolls back: here its refusal.
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.handoff.HandoffRefusedError("handoff control returned HTTP 503"),
        )
        evidence = mocker.Mock(return_value=current)
        with pytest.raises(errors.InstallError, match="returned HTTP 503") as failed:
            self.deploy(
                rolled_back,
                current,
                adapter=rollback_service,
                runtime_reader=evidence,
                mocker=mocker,
            )
        assert not isinstance(failed.value, apply.UnknownDeploymentOutcome)
        assert rolled_back.events == ["commit", "activate", "rollback"]
        rollback_service.install_mock.assert_not_called()
        rollback_service.uninstall_mock.assert_not_called()
        terminate.assert_not_called()

        # A listener that a handoff created reports that finalized session until
        # its next handoff begins; a request that it refused first changes nothing.
        resting = self.current_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.handoff.HandoffRefusedError("handoff control returned HTTP 409"),
        )
        refused = FakeTransaction(self.ctx)
        with pytest.raises(errors.InstallError, match="returned HTTP 409") as failed:
            self.deploy(
                refused,
                resting,
                adapter=FakeServiceAdapter(mocker=mocker),
                runtime_reader=mocker.Mock(return_value=resting),
                mocker=mocker,
            )
        assert not isinstance(failed.value, apply.UnknownDeploymentOutcome)
        assert refused.events == ["commit", "activate", "rollback"]

        unknown = FakeTransaction(self.ctx)
        preserved_service = FakeServiceAdapter(mocker=mocker)
        evidence.reset_mock()
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.UnknownDeploymentOutcome("outcome unknown"),
        )
        with pytest.raises(apply.UnknownDeploymentOutcome, match="outcome unknown"):
            self.deploy(
                unknown,
                current,
                adapter=preserved_service,
                runtime_reader=evidence,
                mocker=mocker,
            )
        assert unknown.events == ["commit", "activate", ("preserve", "outcome unknown")]
        evidence.assert_called_once_with(self.ctx)
        preserved_service.install_mock.assert_not_called()
        control_request.assert_not_called()

    @pytest.mark.parametrize(
        "failure",
        [errors.InstallError("handoff control is unavailable"), KeyboardInterrupt()],
        ids=["no-answer", "interrupt"],
    )
    def test_an_unanswered_handoff_preserves_the_transaction_of_a_resting_listener(
        self, failure: BaseException, *, mocker: MockerFixture
    ) -> None:
        """Without the listener's refusal its finished session does not prove nothing begun.

        The listener may have read the request and be about to begin it, so a payload
        rollback could overlap a handoff that commits its successor.
        """
        payload = FakeTransaction(self.ctx)
        resting = self.current_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(apply, "request_handoff", side_effect=failure)
        control_request = mocker.patch.object(apply.handoff.loopback, "open_request")

        with pytest.raises(apply.UnknownDeploymentOutcome) as raised:
            self.deploy(
                payload,
                resting,
                adapter=service,
                runtime_reader=mocker.Mock(return_value=resting),
                mocker=mocker,
            )

        assert _UNPROVED in str(raised.value)
        assert payload.events == ["commit", "activate", ("preserve", str(raised.value))]
        control_request.assert_not_called()
        service.install_mock.assert_not_called()

    @pytest.mark.parametrize(
        ("evidence", "proof_failure"),
        [
            (
                None,
                _UNPROVED,
            ),
            (
                {"pid": 222},
                _UNPROVED,
            ),
            (
                {"serving_payload_sha256": "9" * 64},
                _UNPROVED,
            ),
            (
                {"accepting": False},
                _UNPROVED,
            ),
            (
                {"draining": True},
                _UNPROVED,
            ),
            (
                {"handoff_state": "ready", "handoff_transaction_id": "7" * 32},
                _UNPROVED,
            ),
            (OSError("offline"), "operating-system operation failed"),
        ],
        ids=[
            "silent",
            "replaced",
            "other-payload",
            "not-accepting",
            "draining",
            "mid-handoff",
            "unreadable",
        ],
    )
    def test_failed_upgrade_preserves_the_transaction_without_a_serving_predecessor(
        self,
        evidence: dict[str, object] | OSError | None,
        proof_failure: str,
        *,
        mocker: MockerFixture,
    ) -> None:
        """The payload rolls back only on proof that the captured predecessor still serves.

        The listener refused the request, so nothing but the runtime evidence withholds the proof.
        """
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        observed = {**current, **evidence} if isinstance(evidence, dict) else evidence
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.handoff.HandoffRefusedError("successor unavailable"),
        )
        control_request = mocker.patch.object(apply.handoff.loopback, "open_request")

        with pytest.raises(apply.UnknownDeploymentOutcome) as raised:
            self.deploy(
                payload,
                current,
                adapter=service,
                runtime_reader=mocker.Mock(side_effect=[current, observed]),
                mocker=mocker,
            )

        assert str(raised.value) == (
            "failed handoff cannot prove that the predecessor still serves; "
            "handoff failure: successor unavailable; "
            f"proof failure: {proof_failure}"
        )
        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                str(raised.value),
            ),
        ]
        control_request.assert_not_called()
        service.install_mock.assert_not_called()
