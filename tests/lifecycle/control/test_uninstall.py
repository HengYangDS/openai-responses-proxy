"""Installed lifecycle uninstall contracts."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import uninstall
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact


def test_purge_removes_the_verified_retained_generation(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    selected = generation.read(ctx)
    assert selected is not None
    assert selected.predecessor is not None
    retained_root = generation.path(ctx, selected.predecessor)
    assert retained_root.is_dir()
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.side_effect = [1, 1]
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])

    result = uninstall.uninstall_product(ctx, purge=True)

    assert result["state"] == "purged"
    assert result["stopped"] == 2
    assert {
        Path(call.args[0].payload_dir).name for call in service.terminate_runtime.call_args_list
    } == {selected.active, selected.predecessor}
    assert not retained_root.exists()
    assert not Path(ctx.install_dir).exists()


def test_purge_stops_and_removes_every_owned_generation(tmp_path: Path, *, mocker) -> None:
    """Purge closes orphan generation processes and bytes before reporting success."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    orphan = "f" * 32
    orphan_root = generation.path(ctx, orphan)
    active = generation.selected_context(ctx)
    shutil.copytree(active.payload_dir, orphan_root)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 1
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])

    result = uninstall.uninstall_product(ctx, purge=True)

    assert result["state"] == "purged"
    stopped = {
        Path(call.args[0].payload_dir).name for call in service.terminate_runtime.call_args_list
    }
    assert orphan in stopped
    assert not Path(ctx.install_dir).exists()


@pytest.mark.parametrize("resume", ["uninstall", "recover"])
@pytest.mark.parametrize("interruption", ["selector", "payload", "metadata", "root", "journal"])
def test_interrupted_purge_resumes_without_deleted_payload_identity(
    tmp_path: Path, resume: str, interruption: str, *, mocker
) -> None:
    """A recorded removal continues after losing part of the active payload."""
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    active = generation.selected_context(ctx)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 0
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    unlink = Path.unlink
    rmdir = Path.rmdir
    executable = Path(active.executable)
    interrupted = {
        "selector": generation.selector_path(ctx),
        "payload": executable,
        "metadata": payload_state.installed_path(ctx),
        "root": Path(ctx.install_dir),
        "journal": payload_state.journal_path(ctx),
    }[interruption]

    def interrupted_unlink(path: Path, *args, **kwargs) -> None:
        if interruption == "journal" and path == interrupted:
            raise PermissionError("interrupted transaction cleanup")
        unlink(path, *args, **kwargs)
        if path == interrupted:
            raise PermissionError("interrupted payload removal")

    def interrupted_rmdir(path: Path, *args, **kwargs) -> None:
        rmdir(path, *args, **kwargs)
        if interruption == "root" and path == interrupted:
            raise PermissionError("interrupted root removal")

    failure = mocker.patch.object(Path, "unlink", interrupted_unlink)
    root_failure = mocker.patch.object(Path, "rmdir", interrupted_rmdir)
    with pytest.raises(errors.InstallError, match="failed"):
        uninstall.uninstall_product(ctx, purge=True)
    mocker.stop(failure)
    mocker.stop(root_failure)
    pending = payload_state.status(ctx)
    assert pending is not None
    assert pending["state"] == "purged"

    result = (
        uninstall.uninstall_product(ctx, purge=True)
        if resume == "uninstall"
        else control.recover(active)
    )

    assert result["state"] == "purged"
    assert not Path(ctx.install_dir).exists()
    assert payload_state.status(ctx) is None


def test_process_teardown_fails_closed_when_exit_is_unproved(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
):
    ctx = install_context(tmp_path_factory.mktemp("case"))
    service = mocker.Mock()
    service.terminate_runtime.side_effect = errors.InstallError(
        "verified runtime process 7 exit is unproved"
    )

    with pytest.raises(errors.InstallError, match="exit is unproved"):
        uninstall._stop_proxy(service, ctx)
    service.terminate_runtime.side_effect = None
    service.terminate_runtime.return_value = 0
    service.terminate_runtime.return_value = 2
    assert uninstall._stop_proxy(service, ctx) == 2


def test_process_teardown_includes_a_replaced_non_listener_generation(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
):
    ctx = install_context(tmp_path_factory.mktemp("case"))
    service = mocker.Mock()
    service.terminate_runtime.return_value = 2

    assert uninstall._stop_proxy(service, ctx) == 2
    service.terminate_runtime.assert_called_once_with(ctx, timeout_seconds=5.0)


def test_process_teardown_visits_one_active_generation_once(tmp_path: Path, *, mocker):
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    selected = generation.read(ctx)
    assert selected is not None
    assert selected.predecessor is None
    active = generation.selected_context(ctx)
    service = mocker.Mock()
    service.terminate_runtime.return_value = 1

    assert uninstall._stop_proxy(service, active) == 1
    service.terminate_runtime.assert_called_once_with(active, timeout_seconds=5.0)


def test_uninstall_product_covers_success_and_fail_closed_boundaries(tmp_path, *, mocker):
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 0
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])
    assert uninstall.uninstall_product(ctx, purge=True) == {
        "state": "purged",
        "stopped": 0,
        "command_removed": True,
    }
    assert not Path(ctx.command).exists()
    assert not Path(ctx.install_dir).exists()
    service.uninstall.assert_called_once_with(ctx)

    service.status.return_value = "loaded"
    with pytest.raises(errors.InstallError, match="remains loaded"):
        uninstall._remove_service(service, ctx)

    service.status.return_value = "absent"
    install_payload(ctx, mocker=mocker)
    assert uninstall.uninstall_product(ctx) == {
        "state": "uninstalled",
        "stopped": 0,
        "command_removed": True,
    }
    assert Path(ctx.install_dir).is_dir()


def test_purge_rejects_control_root_residue_after_owned_generations_are_removed(
    tmp_path: Path, *, mocker
) -> None:
    """Purge cannot report success while unowned control-root content remains."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    residue = Path(ctx.install_dir, "unknown.txt")
    residue.write_text("operator data", encoding="utf-8")
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 1
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])

    with pytest.raises(errors.InstallError, match="unknown install content remains"):
        uninstall.uninstall_product(ctx, purge=True)

    assert residue.read_text(encoding="utf-8") == "operator data"


def test_uninstall_is_idempotent_when_no_installation_exists(tmp_path, *, mocker):
    ctx = install_context(tmp_path)
    service = mocker.Mock()
    service.status.return_value = "absent"
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])

    assert uninstall.uninstall_product(ctx, purge=True) == {
        "state": "not_installed",
        "stopped": 0,
        "command_removed": False,
    }
    assert not Path(ctx.install_dir).exists()


def test_uninstall_refuses_any_retained_transaction_before_mutation(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    service = mocker.Mock()
    service.status.return_value = "absent"
    mocker.patch.object(uninstall, "adapter", return_value=service)
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])

    root = Path(payload_state.transaction_root(ctx))
    root.mkdir(parents=True)
    with pytest.raises(errors.RecoveryStateError, match="invalid"):
        uninstall.uninstall_product(ctx, purge=True)
    service.uninstall.assert_not_called()

    root.rmdir()
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    with pytest.raises(errors.RecoveryRequiredError, match="recovery"):
        uninstall.uninstall_product(ctx, purge=True)
    service.uninstall.assert_not_called()
    transaction.rollback()


def test_status_and_uninstall_use_the_finalized_command_path(tmp_path: Path, *, mocker):
    ctx = install_context(tmp_path)
    installed_command = tmp_path / "original-bin" / "openai-responses-proxy"
    changed_environment_command = tmp_path / "changed-bin" / "openai-responses-proxy"
    ctx = replace(ctx, command=str(changed_environment_command))
    installed_state = {
        "schema_version": payload_state.INSTALLED_RELEASE_STATE_SCHEMA,
        "version": "2.0.37",
        "command": str(installed_command),
    }
    mocker.patch.object(payload_state, "read_installed", return_value=installed_state)
    command_status = mocker.patch.object(
        control.command,
        "status",
        return_value={
            "path": str(installed_command),
            "state": "owned",
            "kind": "symlink",
        },
    )
    mocker.patch.object(control.projection, "verify_payload_manifest", return_value=(True, "ok"))
    mocker.patch.object(control, "adapter").return_value.status.return_value = "running"
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)

    command_evidence = control.status(ctx)["command"]
    assert isinstance(command_evidence, dict)
    assert command_evidence["path"] == str(installed_command)
    command_status.assert_called_once_with(installed_command, Path(ctx.executable))

    service = mocker.patch.object(uninstall, "adapter").return_value
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 0
    mocker.patch.object(uninstall.process, "verified_proxy_listener_pids", return_value=[])
    remove = mocker.patch.object(uninstall.command, "remove", return_value=True)

    uninstall.uninstall_product(ctx)

    remove.assert_called_once_with(installed_command, Path(ctx.executable))


def test_install_and_uninstall_adapters_preserve_bounded_errors(
    *, mocker, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = install_context(tmp_path_factory.mktemp("case"))
    released = mocker.Mock()
    payload_transaction = mocker.Mock()
    service = mocker.Mock()
    admit = mocker.patch.object(install.artifact, "admit", return_value=released)
    begin = mocker.patch.object(
        install.transaction, "begin_transaction", return_value=payload_transaction
    )
    mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.native_service.adapter",
        return_value=service,
    )
    applied = mocker.patch.object(install.apply, "install", return_value={"release": "2.0.15"})
    asset = Path(ctx.install_dir) / "release.tar.gz"
    trust = Path(ctx.install_dir) / "release-trust"

    assert install.install_asset(ctx, asset, trust_anchor=trust, timeout_seconds=4) == {
        "release": "2.0.15"
    }
    admit.assert_called_once_with(asset, trust_anchor=trust)
    begin.assert_called_once_with(ctx, released)
    applied.assert_called_once_with(
        ctx,
        payload_transaction,
        adapter=service,
        runtime_reader=control.read_runtime,
        timeout_seconds=4,
    )

    mocker.patch.object(
        uninstall,
        "adapter",
        side_effect=errors.UnsupportedPlatformError("no host"),
    )
    with pytest.raises(errors.UnsupportedPlatformError, match="no host"):
        uninstall.uninstall_product(ctx)
