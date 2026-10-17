"""Deployment identity admission and public failure boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture
from tests.lifecycle.fixtures import install_context


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_upgrade_requires_the_control_supervisor_before_payload_mutation(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(configured="/retired/launcher", mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        request = mocker.patch.object(apply, "request_handoff")

        with pytest.raises(errors.InstallError, match="lifecycle control executable"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == []
        service.install_mock.assert_not_called()
        request.assert_not_called()

    def test_incompatible_or_unverified_runtime_refuses_before_write(
        self, subtests, *, mocker
    ) -> None:
        for current, captured, message in (
            (
                dict[str, object](pid=111),
                mocker.sentinel.process,
                "incompatible",
            ),
            (
                self.current_runtime(
                    handoff_state="finalized",
                    handoff_transaction_id="txn-previous",
                    serving_payload_sha256=None,
                ),
                mocker.sentinel.process,
                "incompatible",
            ),
            (self.current_runtime(), None, "generation"),
            (self.current_runtime(pid=True), mocker.sentinel.process, "identity"),
        ):
            with subtests.test(message=message):
                payload = FakeTransaction(self.ctx)
                if captured is None:
                    mocker.patch.object(process, "capture_executable", return_value=None)
                with pytest.raises(errors.InstallError, match=message):
                    self.deploy(payload, current, mocker=mocker)
                assert payload.events == []

    def test_unidentified_listener_refuses_fresh_install_before_write(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        mocker.patch.object(process, "listener_pids", return_value=[111])

        with pytest.raises(errors.InstallError, match="identity"):
            self.deploy(payload, None, mocker=mocker)

        assert payload.events == []

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (OSError(13, "private detail"), "operating-system operation failed (OS error 13)"),
            (OSError("private detail"), "operating-system operation failed"),
            (RuntimeError("private detail"), "operation failed"),
            (KeyboardInterrupt("private detail"), "operation failed"),
        ],
    )
    def test_public_failure_projection_does_not_expose_exception_types(
        self, failure: BaseException, expected: str
    ) -> None:
        assert errors.failure_summary(failure) == expected


def test_install_has_no_json_publication_proof_loader() -> None:
    assert not hasattr(install, "publication_proof_from_file")


def test_install_admission_failure_closes_the_prepared_transaction(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    payload = mocker.Mock()
    payload.rollback_if_prepared.return_value = True
    mocker.patch.object(install.artifact, "admit", return_value="released")
    mocker.patch.object(install.transaction, "begin_transaction", return_value=payload)
    mocker.patch.object(
        install.apply,
        "install",
        side_effect=errors.InstallError("installed runtime identity is not verified"),
    )

    with pytest.raises(errors.InstallError, match="identity is not verified"):
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
        )

    payload.rollback_if_prepared.assert_called_once_with()


@pytest.mark.parametrize("unavailable", [False, True])
def test_install_admission_failure_retains_failed_prepared_cleanup(
    tmp_path: Path, unavailable: bool, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    payload = mocker.Mock()
    failure_type = errors.NativeServiceUnavailableError if unavailable else errors.InstallError
    initial = failure_type("initial installation boundary failed")
    payload.rollback_if_prepared.side_effect = errors.InstallError(
        "payload generation removal failed (OS error 32)"
    )
    mocker.patch.object(install.artifact, "admit", return_value="released")
    mocker.patch.object(install.transaction, "begin_transaction", return_value=payload)
    mocker.patch.object(install.apply, "install", side_effect=initial)

    with pytest.raises(failure_type) as raised:
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
        )

    assert str(raised.value) == (
        "installation failed: initial installation boundary failed; "
        "prepared payload cleanup failed: payload generation removal failed (OS error 32)"
    )
    assert raised.value.code == initial.code
    assert raised.value.next_command == initial.next_command
    payload.rollback_if_prepared.assert_called_once_with()
