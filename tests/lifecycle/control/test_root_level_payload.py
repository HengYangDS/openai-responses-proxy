"""Payload files at the control root's own level are unverified content, not an installation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import uninstall
from openai_responses_proxy.lifecycle.transaction import completion
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import control_root
from tests.lifecycle.fixtures import listing_with_digests
from tests.lifecycle.fixtures import root_level_payload

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

NO_SELECTION = {"ok": False, "detail": "no payload generation is selected"}


def _root_level_executable(ctx: runtime_context.InstallationContext) -> Path:
    return Path(inventory.installed_executable(ctx.install_dir))


def _healthy_at_the_root(
    ctx: runtime_context.InstallationContext, *, mocker: MockerFixture
) -> None:
    """Project a running supervisor and an accepting listener of the root-level payload."""
    served = identity.committed_payload(_root_level_executable(ctx))
    assert served is not None
    native = cast("MockType", mocker.patch.object(control, "adapter").return_value)
    native.status.return_value = "running"
    native.configured_executable.return_value = str(_root_level_executable(ctx))
    native.projection_is_current.return_value = True
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[321])
    mocker.patch.object(
        control,
        "read_runtime",
        return_value={
            "pid": 321,
            **served.handoff(),
            "payload_manifest_sha256": served.manifest_sha256,
            "accepting": True,
            "draining": False,
        },
    )


def _stopped_supervisor(
    ctx: runtime_context.InstallationContext, *, mocker: MockerFixture
) -> MockType:
    """Project a registered supervisor that declares the root-level executable and runs nothing."""
    native = cast("MockType", mocker.patch.object(control, "adapter").return_value)
    native.status.return_value = "installed"
    native.configured_executable.return_value = str(_root_level_executable(ctx))
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control.process, "listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    return native


@pytest.mark.parametrize("supervisor", ["running", "stopped"])
def test_status_reports_no_installation_for_a_payload_at_the_control_root(
    tmp_path: Path, supervisor: str, *, mocker: MockerFixture
) -> None:
    """A manifest, a carrier and an installed record do not stand in for a selection."""
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    if supervisor == "running":
        _healthy_at_the_root(ctx, mocker=mocker)
    else:
        _stopped_supervisor(ctx, mocker=mocker)
    before = listing_with_digests(tmp_path)

    observed = control.status(ctx)

    assert observed["state"] == "degraded"
    assert observed["detail"] == "no payload generation is selected"
    assert observed["payload_integrity"] == NO_SELECTION
    assert observed["runtime"] is None
    assert observed["listener_pids"] == []
    command_state = observed["command"]
    assert isinstance(command_state, dict)
    assert command_state["state"] == "foreign"
    assert listing_with_digests(tmp_path) == before


def test_the_command_line_context_reaches_status_for_a_payload_at_the_control_root(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """The context that every command creates names no payload, and `status` verifies none.

    The root-level carrier is not looked for, and neither the root-level manifest nor a
    listener that serves it gives `status` a payload: the release that it names is the
    installed record's alone, and without that record it names none.
    """
    control_ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    _healthy_at_the_root(control_ctx, mocker=mocker)
    mocker.patch.object(config, "home_dir", return_value=control_ctx.user_home)
    mocker.patch.object(config, "data_dir", return_value=control_ctx.install_dir)
    mocker.patch.object(config, "state_dir", return_value=control_ctx.log_dir)
    manifest = mocker.spy(control.projection, "verify_payload_manifest")
    committed = mocker.spy(control.identity, "committed_payload")
    listeners = mocker.spy(control.process, "verified_proxy_listener_pids")

    ctx = runtime_context.create(port=control_ctx.port)

    assert type(ctx) is runtime_context.InstallationContext
    assert generation.selected_context(ctx) is None
    assert generation.control_context(ctx) is None
    observed = control.status(ctx)
    assert observed["state"] == "degraded"
    assert observed["payload_integrity"] == NO_SELECTION
    assert observed["runtime"] is None
    assert observed["release"] == "1.2.3"
    records.installed_path(ctx).unlink()
    without_record = control.status(ctx)
    assert without_record["state"] == "degraded"
    assert without_record["payload_integrity"] == NO_SELECTION
    assert without_record["release"] is None
    manifest.assert_not_called()
    committed.assert_not_called()
    listeners.assert_not_called()


def test_rollback_proves_no_release_active_at_the_control_root_and_changes_no_file(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    _healthy_at_the_root(ctx, mocker=mocker)
    apply = mocker.patch.object(control.apply, "rollback")
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.RollbackStateError, match="not proven active"):
        control.rollback(ctx, to_release="1.2.3")
    assert control.rollback(ctx, to_release="1.2.2") == {
        "state": "unavailable",
        "detail": "no verified predecessor is retained",
    }

    apply.assert_not_called()
    assert listing_with_digests(tmp_path) == before


def test_rollback_requires_a_selection_whatever_status_reports(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """Without a selection no release is active, whatever status reports."""
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    mocker.patch.object(control, "status", return_value={"state": "running", "release": "1.2.3"})

    with pytest.raises(errors.RollbackStateError, match="not proven active"):
        control.rollback(ctx, to_release="1.2.3")


def test_an_installed_record_names_no_payload_without_a_selection(tmp_path: Path) -> None:
    """The record is bound to the selection where it is compared, not only by its callers.

    A payload identity read before the selector was removed still names no installed payload.
    """
    ctx = control_root(tmp_path)
    committed = identity.LoadedPayloadIdentity("1.2.3", "a" * 64, "b" * 64, "c" * 64, tmp_path)
    installed = {
        "version": "1.2.3",
        "receipt_sha256": "b" * 64,
        "command": ctx.command,
        "transaction_id": "5" * 32,
    }

    assert generation.read(ctx) is None
    assert not control.installed_names(ctx, installed, committed)


def test_reload_starts_nothing_for_a_payload_at_the_control_root(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    native = _stopped_supervisor(ctx, mocker=mocker)
    wait = mocker.patch.object(
        control.apply, "wait_for_serving_runtime", return_value={"pid": 4242}
    )
    request = mocker.patch.object(control.handoff, "request")
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="not healthy enough to reload"):
        control.reload(ctx)

    native.install.assert_not_called()
    wait.assert_not_called()
    request.assert_not_called()
    assert listing_with_digests(tmp_path) == before


def test_purge_preserves_every_byte_of_a_payload_at_the_control_root(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """Purge stops before it writes a journal: nothing there is a generation that it owns."""
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    before = listing_with_digests(tmp_path)
    assert {name for name, _ in listing_with_digests(Path(ctx.install_dir))} == {
        "bin",
        inventory.EXECUTABLE,
        inventory.PROVIDER_MANIFEST,
        inventory.MANIFEST_FILENAME,
        inventory.RELEASE_RECEIPT_FILENAME,
        inventory.RUNTIME_CONFIG_FILENAME,
        inventory.INSTALLED_RELEASE_STATE_FILENAME,
    }

    with pytest.raises(errors.InstallError, match="holds no payload generation") as refused:
        completion.purge(ctx)

    assert "preserved" in str(refused.value)
    assert listing_with_digests(tmp_path) == before
    assert not records.journal_path(ctx).exists()
    assert records.status(ctx) is None


def test_uninstall_removes_no_command_that_names_a_payload_at_the_control_root(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """Without a selection the command is nothing that uninstall can prove, and it stops there."""
    ctx = root_level_payload(control_root(tmp_path), mocker=mocker)
    service = mocker.patch.object(uninstall, "adapter").return_value
    service.status.return_value = "absent"
    listeners = mocker.spy(uninstall.process, "verified_proxy_listener_pids")
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        uninstall.uninstall_product(ctx, purge=True)

    service.uninstall.assert_called_once_with(ctx, [])
    service.terminate_runtime.assert_not_called()
    listeners.assert_not_called()
    assert listing_with_digests(tmp_path) == before
    assert records.status(ctx) is None

    Path(ctx.command).unlink()
    kept = listing_with_digests(tmp_path)
    with pytest.raises(errors.InstallError, match="holds no payload generation"):
        uninstall.uninstall_product(ctx, purge=True)
    assert listing_with_digests(tmp_path) == kept
    assert uninstall.uninstall_product(ctx) == {
        "state": "uninstalled",
        "stopped": 0,
        "command_removed": False,
    }
    assert listing_with_digests(tmp_path) == kept
