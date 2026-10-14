"""Installed lifecycle reload contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import identity
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload


def _stopped_installation(root: Path, *, mocker):
    """Install one verified payload whose owned supervisor and listener are stopped."""
    ctx = install_context(root)
    install_payload(ctx, mocker=mocker)
    control_ctx = generation.control_context(ctx)
    native = mocker.patch.object(control, "adapter").return_value
    native.status.return_value = "installed"
    native.configured_executable.return_value = control_ctx.executable
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control.process, "listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "owned", "kind": "symlink"},
    )
    return ctx, control_ctx, native


def _serving_installation(root: Path, *, service: str, mocker):
    """Install one verified payload whose listener serves under the observed supervisor."""
    ctx = install_context(root)
    install_payload(ctx, mocker=mocker)
    control_ctx = generation.control_context(ctx)
    native = mocker.patch.object(control, "adapter").return_value
    native.status.return_value = service
    native.configured_executable.return_value = control_ctx.executable
    mocker.patch.object(control, "read_runtime", return_value={"pid": 7})
    mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
    mocker.patch.object(
        control.handoff,
        "capture_source_listener",
        return_value=control.process.OwnedProcess(7, ctx.executable, 1.0),
    )
    return ctx, control_ctx, native


def test_control_reads_bounded_runtime_and_recovers_finalized_reload(
    subtests, *, mocker, tmp_path_factory: pytest.TempPathFactory
):
    ctx = install_context(tmp_path_factory.mktemp("case"))
    Path(ctx.install_dir).mkdir(parents=True)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"pid": 7}'

    open_request = mocker.patch.object(
        control.loopback,
        "open_request",
        return_value=Response(),
    )
    assert control.read_runtime(ctx) == {"pid": 7}
    open_request.side_effect = OSError("offline")
    assert control.read_runtime(ctx) is None
    open_request.side_effect = None
    open_request.return_value = Response()
    open_request.return_value.status = 503
    assert control.read_runtime(ctx) is None

    runtime = {"pid": 7}
    expected = {"transaction_id": "tx"}
    native = mocker.patch.object(control, "adapter").return_value
    native.configured_executable.return_value = ctx.executable
    mocker.patch.object(control, "read_runtime", return_value=runtime)
    mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
    mocker.patch.object(control.projection, "verify_payload_manifest", return_value=(True, "ok"))
    mocker.patch.object(control.handoff, "expected_metadata", return_value=expected)
    mocker.patch.object(
        control.handoff,
        "capture_source_listener",
        return_value=control.process.OwnedProcess(7, ctx.executable, 1.0),
    )
    mocker.patch.object(control.handoff, "request", side_effect=OSError("lost response"))
    mocker.patch.object(
        control.handoff,
        "resolve_after_controller_failure",
        return_value=("finalized", {"pid": 8}),
    )
    assert control.reload(ctx) == {
        "state": "reloaded",
        "old_pid": 7,
        "new_pid": 8,
        "transaction_id": "tx",
        "recovered_after_controller_failure": True,
    }
    mocker.patch.object(control, "read_runtime", return_value=runtime)
    mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(False, "tampered"),
    )

    with pytest.raises(errors.InstallError, match="tampered"):
        control.reload(ctx)

    for resolution, expected_error in (
        ("unknown", errors.InstallError),
        ("rolled_back", OSError),
    ):
        mocker.patch.object(control, "read_runtime", return_value=runtime)
        mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            control.projection,
            "verify_payload_manifest",
            return_value=(True, "ok"),
        )
        mocker.patch.object(control.handoff, "expected_metadata", return_value=expected)
        mocker.patch.object(control.handoff, "request", side_effect=OSError("lost response"))
        mocker.patch.object(
            control.handoff,
            "resolve_after_controller_failure",
            return_value=(resolution, None),
        )
        with subtests.test(resolution=resolution), pytest.raises(expected_error):
            control.reload(ctx)


def test_reload_reads_handoff_identity_from_selected_generation(tmp_path, *, mocker):
    stable = install_context(tmp_path)
    Path(stable.install_dir).mkdir(parents=True)
    ctx = generation.context(stable, "a" * 32)
    runtime = {"pid": 7}
    expected = {"transaction_id": "tx"}
    native = mocker.patch.object(control, "adapter").return_value
    native.configured_executable.return_value = ctx.executable
    mocker.patch.object(control.records, "read_installed", return_value={})
    mocker.patch.object(control.records, "status", return_value=None)
    mocker.patch.object(control, "read_runtime", return_value=runtime)
    mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(True, "ok"),
    )
    expected_metadata = mocker.patch.object(
        control.handoff,
        "expected_metadata",
        return_value=expected,
    )
    mocker.patch.object(
        control.handoff,
        "capture_source_listener",
        return_value=control.process.OwnedProcess(7, ctx.executable, 1.0),
    )
    mocker.patch.object(
        control.handoff,
        "request",
        return_value={"old_pid": 7, "child_pid": 8},
    )

    assert control.reload(ctx)["new_pid"] == 8
    expected_metadata.assert_called_once_with(ctx.payload_dir)


def test_status_and_reload_bound_unobservable_failures(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = install_context(tmp_path_factory.mktemp("case"))
    Path(ctx.install_dir).mkdir(parents=True)
    current_error = errors.InstallError("current manifest invalid")
    mocker.patch.object(control.projection, "verify_payload_manifest", side_effect=current_error)
    mocker.patch.object(
        control,
        "adapter",
        side_effect=errors.InstallError("service unavailable"),
    )
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    evidence = control.status(ctx)
    assert evidence["payload_integrity"] == {
        "ok": False,
        "detail": str(current_error),
    }
    assert evidence["service"] == "unknown"

    mocker.patch.object(
        control,
        "adapter",
        side_effect=errors.ProductAssemblyError("product assembly incomplete"),
    )
    with pytest.raises(errors.ProductAssemblyError, match="product assembly incomplete"):
        control.status(ctx)

    native = mocker.patch.object(control, "adapter").return_value
    native.configured_executable.return_value = ctx.executable
    mocker.patch.object(control, "read_runtime", return_value={"pid": 7})
    mocker.patch.object(control.handoff, "runtime_supports_handoff", return_value=True)
    mocker.patch.object(control.projection, "verify_payload_manifest", return_value=(True, "ok"))
    mocker.patch.object(control.handoff, "expected_metadata", return_value={"transaction_id": "tx"})
    mocker.patch.object(
        control.handoff,
        "capture_source_listener",
        return_value=control.process.OwnedProcess(7, ctx.executable, 1.0),
    )
    mocker.patch.object(control.handoff, "request", side_effect=OSError("lost response"))
    mocker.patch.object(
        control.handoff,
        "resolve_after_controller_failure",
        side_effect=RuntimeError("resolution unavailable"),
    )
    with pytest.raises(errors.InstallError, match="outcome is unconfirmed"):
        control.reload(ctx)


def test_reload_requires_installation_and_resolved_transaction_state(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)

    with subtests.test(state="absent"), pytest.raises(errors.NotInstalledError):
        control.reload(ctx)

    Path(ctx.install_dir).mkdir(parents=True)
    for transaction_state, expected in (
        ({"state": "invalid"}, errors.RecoveryStateError),
        ({"state": "prepared"}, errors.RecoveryRequiredError),
    ):
        mocker.patch.object(records, "status", return_value=transaction_state)
        with (
            subtests.test(state=transaction_state["state"]),
            pytest.raises(expected),
        ):
            control.reload(ctx)


def test_reload_refuses_incompatible_listener_without_mutation(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
):
    ctx = install_context(tmp_path_factory.mktemp("case"))
    Path(ctx.install_dir).mkdir(parents=True)
    mocker.patch.object(control, "read_runtime", return_value={"pid": 12345})
    terminate = mocker.patch.object(process, "terminate_pid")
    with pytest.raises(errors.InstallError, match="not healthy enough to reload"):
        control.reload(ctx)
    terminate.assert_not_called()


def test_reload_starts_a_stopped_installation_through_its_native_supervisor(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native = _stopped_installation(tmp_path, mocker=mocker)
    committed = identity.committed_payload(Path(ctx.executable))
    assert committed is not None
    serving = {"pid": 4242, "accepting": True, "draining": False}
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime", return_value=serving)
    request = mocker.patch.object(control.handoff, "request")

    assert control.reload(ctx, timeout_seconds=12.5) == {"state": "started", "new_pid": 4242}
    native.install.assert_called_once_with(control_ctx)
    wait.assert_called_once_with(
        ctx,
        committed.handoff(),
        runtime_reader=control.read_runtime,
        timeout_seconds=12.5,
    )
    request.assert_not_called()
    native.uninstall.assert_not_called()


@pytest.mark.parametrize(
    ("service", "configured", "listeners"),
    [
        ("running", "control", []),
        ("absent", "control", []),
        ("installed", "foreign", []),
        ("installed", "control", [4242]),
    ],
)
def test_reload_refuses_a_runtime_less_installation_that_is_not_stopped(
    tmp_path: Path, service: str, configured: str, listeners: list[int], *, mocker
) -> None:
    ctx, control_ctx, native = _stopped_installation(tmp_path, mocker=mocker)
    native.status.return_value = service
    native.configured_executable.return_value = (
        control_ctx.executable if configured == "control" else str(tmp_path / "foreign")
    )
    mocker.patch.object(control.process, "listener_pids", return_value=listeners)
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime")

    with pytest.raises(errors.InstallError, match="not healthy enough to reload") as refused:
        control.reload(ctx)

    assert refused.value.next_command == product_identity.command("doctor")
    native.install.assert_not_called()
    wait.assert_not_called()


def test_reload_requires_the_started_supervisor_to_bind_the_control_executable(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native = _stopped_installation(tmp_path, mocker=mocker)
    native.configured_executable.side_effect = [control_ctx.executable, str(tmp_path / "foreign")]
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime")

    with pytest.raises(errors.InstallError, match="did not bind the lifecycle control executable"):
        control.reload(ctx)

    native.install.assert_called_once_with(control_ctx)
    wait.assert_not_called()


def test_reload_keeps_native_supervision_when_a_started_listener_is_unproved(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native = _stopped_installation(tmp_path, mocker=mocker)
    mocker.patch.object(
        control.apply,
        "wait_for_serving_runtime",
        side_effect=errors.InstallError("released successor did not prove SERVING identity"),
    )

    with pytest.raises(errors.InstallError, match="native supervisor started") as unproved:
        control.reload(ctx, timeout_seconds=12.5)

    assert "12.5s" in str(unproved.value)
    native.install.assert_called_once_with(control_ctx)
    native.uninstall.assert_not_called()
    native.terminate_runtime.assert_not_called()


def test_reload_refuses_a_stopped_payload_that_changes_before_start(
    tmp_path: Path, *, mocker
) -> None:
    ctx, _control_ctx, native = _stopped_installation(tmp_path, mocker=mocker)
    mocker.patch.object(control, "status", return_value={"state": "stopped"})
    mocker.patch.object(control.identity, "committed_payload", return_value=None)

    with pytest.raises(errors.InstallError, match="changed before supervised start"):
        control.reload(ctx)

    native.install.assert_not_called()


@pytest.mark.parametrize("service", ["installed", "running"])
def test_reload_rebinds_native_supervision_after_a_proven_handoff(
    tmp_path: Path, service: str, *, mocker
) -> None:
    ctx, control_ctx, native = _serving_installation(tmp_path, service=service, mocker=mocker)
    effects: list[str] = []

    def hand_off(*_args, **_kwargs) -> dict[str, int]:
        effects.append("handoff")
        return {"old_pid": 7, "child_pid": 8}

    mocker.patch.object(control.handoff, "request", side_effect=hand_off)
    native.install.side_effect = lambda _ctx: effects.append("bind")

    assert control.reload(ctx) == {"state": "reloaded", "old_pid": 7, "new_pid": 8}
    assert effects == ["handoff", "bind"]
    native.install.assert_called_once_with(control_ctx)
    native.uninstall.assert_not_called()


def test_reload_rebinds_native_supervision_after_a_recovered_handoff(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native = _serving_installation(tmp_path, service="installed", mocker=mocker)
    effects: list[str] = []

    def resolve(*_args, **_kwargs) -> tuple[str, dict[str, int]]:
        effects.append("resolve")
        return "finalized", {"pid": 8}

    mocker.patch.object(control.handoff, "request", side_effect=OSError("lost response"))
    mocker.patch.object(control.handoff, "resolve_after_controller_failure", side_effect=resolve)
    native.install.side_effect = lambda _ctx: effects.append("bind")

    result = control.reload(ctx)

    assert result["state"] == "reloaded"
    assert (result["old_pid"], result["new_pid"]) == (7, 8)
    assert result["recovered_after_controller_failure"] is True
    assert effects == ["resolve", "bind"]
    native.install.assert_called_once_with(control_ctx)


@pytest.mark.parametrize("configured", ["missing", "foreign"])
def test_reload_refuses_an_unbound_supervisor_before_the_handoff(
    tmp_path: Path, configured: str, *, mocker
) -> None:
    ctx, _control_ctx, native = _serving_installation(tmp_path, service="installed", mocker=mocker)
    native.configured_executable.return_value = (
        None if configured == "missing" else str(tmp_path / "foreign" / "openai-responses-proxy")
    )
    request = mocker.patch.object(control.handoff, "request")

    with pytest.raises(errors.InstallError, match="not bound to the lifecycle control") as refused:
        control.reload(ctx)

    assert refused.value.next_command == product_identity.command("status", "--json")
    request.assert_not_called()
    native.install.assert_not_called()


def test_reload_reports_a_supervisor_rebind_failure_after_the_handoff(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native = _serving_installation(tmp_path, service="installed", mocker=mocker)
    mocker.patch.object(control.handoff, "request", return_value={"old_pid": 7, "child_pid": 8})
    native.install.side_effect = errors.InstallError("launchctl bootstrap failed (exit 5)")

    with pytest.raises(errors.InstallError) as failed:
        control.reload(ctx)

    assert "listener 8" in str(failed.value)
    assert "launchctl bootstrap failed (exit 5)" in str(failed.value)
    assert failed.value.next_command == product_identity.command("doctor")
    native.install.assert_called_once_with(control_ctx)
    native.uninstall.assert_not_called()
    native.terminate_runtime.assert_not_called()
