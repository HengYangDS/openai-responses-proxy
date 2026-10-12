"""Installed lifecycle recovery contracts."""

from __future__ import annotations

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from tests.lifecycle.fixtures import install_context


def test_recovery_rebinds_supervision_to_the_control_generation(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = install_context(tmp_path_factory.mktemp("case"))
    control_generation = generation.context(ctx, "a" * 32)
    service = mocker.Mock()
    service.configured_executable.return_value = control_generation.executable
    mocker.patch.object(control.payload_state, "status", return_value={"state": "activated"})
    mocker.patch.object(control, "read_runtime", return_value={"pid": 7})
    recover = mocker.patch.object(control.transaction, "recover")
    mocker.patch.object(control, "adapter", return_value=service)

    def recover_and_bind(_ctx, *, runtime, bind_terminal, discard_native):
        assert runtime == {"pid": 7}
        assert callable(discard_native)
        bind_terminal(control_generation)
        return {"state": "finalized", "version": "3.1.3"}

    recover.side_effect = recover_and_bind

    assert control.recover(ctx) == {"state": "finalized", "version": "3.1.3"}

    recover.assert_called_once()
    assert recover.call_args.kwargs["runtime"] == {"pid": 7}
    service.install.assert_called_once_with(control_generation)
    service.configured_executable.assert_called_once_with(control_generation)


def test_recovery_rejects_an_unproved_terminal_supervisor(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = install_context(tmp_path_factory.mktemp("case"))
    control_generation = generation.context(ctx, "a" * 32)
    service = mocker.Mock()
    service.configured_executable.return_value = None
    mocker.patch.object(control.payload_state, "status", return_value={"state": "activated"})
    mocker.patch.object(control, "read_runtime", return_value={"pid": 7})
    recover = mocker.patch.object(control.transaction, "recover")
    mocker.patch.object(control, "adapter", return_value=service)

    def recover_and_bind(_ctx, *, runtime, bind_terminal, discard_native):
        assert runtime == {"pid": 7}
        assert callable(discard_native)
        bind_terminal(control_generation)

    recover.side_effect = recover_and_bind

    with pytest.raises(errors.RecoveryStateError, match="supervisor identity"):
        control.recover(ctx)


def test_recovery_no_op_does_not_touch_native_supervision(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = install_context(tmp_path_factory.mktemp("case"))
    mocker.patch.object(control.payload_state, "status", return_value=None)
    mocker.patch.object(control, "read_runtime", return_value=None)
    recover = mocker.patch.object(
        control.transaction,
        "recover",
        return_value={"state": "not_required"},
    )
    native = mocker.patch.object(control, "adapter")

    assert control.recover(ctx) == {"state": "not_required"}

    recover.assert_called_once()
    native.assert_not_called()


@pytest.mark.parametrize("failure", [None, "wrong-supervisor", "registered", "process-exit"])
def test_recovery_stops_only_its_candidate_before_native_disposal(
    failure, tmp_path, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = generation.context(ctx, "a" * 32)
    service = mocker.Mock()
    service.configured_executable.return_value = (
        generation.context(ctx, "b" * 32).executable
        if failure == "wrong-supervisor"
        else candidate.executable
    )
    service.status.return_value = "running" if failure == "registered" else "absent"
    if failure == "process-exit":
        service.terminate_runtime.side_effect = errors.InstallError("process exit unproved")
    mocker.patch.object(control, "adapter", return_value=service)
    mocker.patch.object(control.payload_state, "status", return_value={"state": "activated"})
    mocker.patch.object(control, "read_runtime", return_value=None)

    def recover_and_discard(_ctx, *, runtime, bind_terminal, discard_native):
        assert runtime is None
        assert callable(bind_terminal)
        discard_native(candidate)
        return {"state": "rolled_back"}

    mocker.patch.object(control.transaction, "recover", side_effect=recover_and_discard)
    if failure is None:
        assert control.recover(ctx) == {"state": "rolled_back"}
        service.uninstall.assert_called_once_with(candidate)
        service.terminate_runtime.assert_called_once_with(candidate, timeout_seconds=5.0)
    else:
        with pytest.raises(control.apply.UnknownDeploymentOutcome, match="cleanup is unconfirmed"):
            control.recover(ctx)
        if failure == "wrong-supervisor":
            service.uninstall.assert_not_called()
        if failure in {"wrong-supervisor", "registered"}:
            service.terminate_runtime.assert_not_called()


@pytest.mark.parametrize("native_state", ["absent", "running", "installed", "unknown"])
def test_recovery_requires_native_absence_when_candidate_identity_is_missing(
    native_state, tmp_path, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = generation.context(ctx, "a" * 32)
    service = mocker.Mock()
    service.configured_executable.return_value = None
    service.status.return_value = native_state
    mocker.patch.object(control, "adapter", return_value=service)
    mocker.patch.object(control.payload_state, "status", return_value={"state": "activated"})
    mocker.patch.object(control, "read_runtime", return_value=None)

    def recover_and_discard(_ctx, *, runtime, bind_terminal, discard_native):
        assert runtime is None
        assert callable(bind_terminal)
        discard_native(candidate)
        return {"state": "rolled_back"}

    mocker.patch.object(control.transaction, "recover", side_effect=recover_and_discard)
    if native_state == "absent":
        assert control.recover(ctx) == {"state": "rolled_back"}
        service.terminate_runtime.assert_called_once_with(candidate, timeout_seconds=5.0)
    else:
        with pytest.raises(control.apply.UnknownDeploymentOutcome, match="cleanup is unconfirmed"):
            control.recover(ctx)
        service.terminate_runtime.assert_not_called()
    service.uninstall.assert_not_called()
