"""What `status`, `reload` and `uninstall` do on a control root that selects no generation.

The context of every test is the control root's alone: it names no payload, so a command
that read one here would fail on the attribute and not on an assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import uninstall
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import control_root
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import listing_with_digests
from tests.lifecycle.fixtures import released_artifact
from tests.serving.handoff.fixtures import idle_runtime

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock.plugin import SpyType

    from openai_responses_proxy.lifecycle import context as runtime_context

STATES = ("absent", "empty", "unverified")
NO_SELECTION = "no payload generation is selected"
NOT_INSTALLED = {"state": "not_installed", "stopped": 0, "command_removed": False}


def _control_root_in(root: Path, state: str) -> runtime_context.InstallationContext:
    """Return a control root that is absent, empty, or holds content that nothing verifies."""
    ctx = control_root(root)
    if state != "absent":
        Path(ctx.install_dir).mkdir(parents=True)
    if state == "unverified":
        Path(ctx.install_dir, "operator-notes.txt").write_text("operator data", encoding="utf-8")
    return ctx


def _another_program_at(ctx: runtime_context.InstallationContext) -> Path:
    """Put a file that this product did not write at the command path."""
    command_path = Path(ctx.command)
    command_path.parent.mkdir(parents=True)
    command_path.write_text("another program", encoding="utf-8")
    return command_path


def _payload_readers(mocker: MockerFixture) -> tuple[SpyType, ...]:
    """Watch every reader of a payload location that `status` and `reload` have."""
    return (
        mocker.spy(control.projection, "verify_payload_manifest"),
        mocker.spy(control.identity, "committed_payload"),
        mocker.spy(control.process, "verified_proxy_listener_pids"),
        mocker.spy(control.process, "capture_executable"),
    )


@pytest.mark.parametrize("state", STATES)
def test_status_reads_no_payload_location_without_a_selection(
    tmp_path: Path, state: str, *, mocker: MockerFixture
) -> None:
    ctx = _control_root_in(tmp_path, state)
    native = mocker.patch.object(control, "adapter").return_value
    native.status.return_value = "absent"
    mocker.patch.object(control, "read_runtime", return_value=None)
    readers = _payload_readers(mocker)
    before = listing_with_digests(tmp_path)

    observed = control.status(ctx)

    if state == "absent":
        assert (observed["state"], observed["detail"]) == ("not_installed", "not installed")
        assert observed["payload_integrity"] == {"ok": False, "detail": "not installed"}
    else:
        assert (observed["state"], observed["detail"]) == ("degraded", NO_SELECTION)
        assert observed["payload_integrity"] == {"ok": False, "detail": NO_SELECTION}
    assert observed["command"] == {"state": "absent", "kind": None, "path": ctx.command}
    assert observed["listener_pids"] == []
    assert observed["runtime"] is None
    assert observed["release"] is None
    assert observed["payload_transaction"] is None
    assert observed["rollback"] == {"state": "unavailable"}
    for reader in readers:
        reader.assert_not_called()
    native.configured_executable.assert_not_called()
    native.projection_is_current.assert_not_called()
    assert listing_with_digests(tmp_path) == before


@pytest.mark.parametrize("state", STATES)
def test_status_owns_no_command_listener_or_supervisor_without_a_selection(
    tmp_path: Path, state: str, *, mocker: MockerFixture
) -> None:
    """Whatever is at the command path, answers on the port or runs the service is not ours."""
    ctx = _control_root_in(tmp_path, state)
    _another_program_at(ctx)
    native = mocker.patch.object(control, "adapter").return_value
    native.status.return_value = "running"
    native.configured_executable.return_value = str(tmp_path / "elsewhere" / "proxy")
    mocker.patch.object(control, "read_runtime", return_value=idle_runtime(pid=4242))
    readers = _payload_readers(mocker)

    observed = control.status(ctx)

    assert (observed["state"], observed["detail"]) == ("degraded", NO_SELECTION)
    assert observed["payload_integrity"] == {"ok": False, "detail": NO_SELECTION}
    assert observed["command"] == {"state": "foreign", "kind": None, "path": ctx.command}
    assert observed["service"] == "running"
    assert observed["listener_pids"] == []
    assert observed["runtime"] is None
    for reader in readers:
        reader.assert_not_called()
    native.projection_is_current.assert_not_called()


@pytest.mark.parametrize("reached", ["prepared", "materialized"])
def test_status_of_a_first_installation_in_progress_asks_for_recovery(
    tmp_path: Path, reached: str, *, mocker: MockerFixture
) -> None:
    ctx = control_root(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    if reached == "materialized":
        transaction.commit_projection()
    mocker.patch.object(control, "adapter").return_value.status.return_value = "absent"
    mocker.patch.object(control, "read_runtime", return_value=None)

    observed = control.status(ctx)

    assert generation.read(ctx) is None
    assert (observed["state"], observed["detail"]) == (
        "recovery_required",
        "payload recovery is required",
    )
    assert observed["payload_integrity"] == {"ok": False, "detail": NO_SELECTION}
    assert observed["command"] == {"state": "absent", "kind": None, "path": ctx.command}
    transaction.rollback()


@pytest.mark.parametrize("state", STATES)
def test_reload_starts_and_hands_off_nothing_without_a_selection(
    tmp_path: Path, state: str, *, mocker: MockerFixture
) -> None:
    ctx = _control_root_in(tmp_path, state)
    native = mocker.patch.object(control, "adapter").return_value
    native.status.return_value = "installed"
    mocker.patch.object(control.process, "listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    request = mocker.patch.object(control.handoff, "request")
    readers = _payload_readers(mocker)
    before = listing_with_digests(tmp_path)

    if state == "absent":
        with pytest.raises(errors.NotInstalledError):
            control.reload(ctx)
    else:
        with pytest.raises(errors.InstallError, match="not healthy enough to reload"):
            control.reload(ctx)

    native.install.assert_not_called()
    request.assert_not_called()
    for reader in readers:
        reader.assert_not_called()
    assert listing_with_digests(tmp_path) == before


@pytest.mark.parametrize("state", ["empty", "unverified"])
def test_reload_names_the_missing_selection_where_a_listener_answers(
    tmp_path: Path, state: str, *, mocker: MockerFixture
) -> None:
    """A listener that could hand off is not this installation's: there is no payload to verify."""
    ctx = _control_root_in(tmp_path, state)
    native = mocker.patch.object(control, "adapter").return_value
    mocker.patch.object(control, "read_runtime", return_value=idle_runtime())
    request = mocker.patch.object(control.handoff, "request")
    readers = _payload_readers(mocker)

    with pytest.raises(errors.InstallError) as refused:
        control.reload(ctx)

    assert str(refused.value) == (
        f"payload integrity check failed before handoff reload: {NO_SELECTION}"
    )
    request.assert_not_called()
    native.install.assert_not_called()
    native.configured_executable.assert_not_called()
    for reader in readers:
        reader.assert_not_called()


@pytest.mark.parametrize("purge", [False, True], ids=["uninstall", "purge"])
@pytest.mark.parametrize("state", STATES)
def test_uninstall_without_a_selection_removes_the_service_and_no_file(
    tmp_path: Path, state: str, *, purge: bool, mocker: MockerFixture
) -> None:
    ctx = _control_root_in(tmp_path, state)
    service = mocker.patch.object(uninstall, "adapter").return_value
    service.status.return_value = "absent"
    listeners = mocker.spy(uninstall.process, "verified_proxy_listener_pids")
    before = listing_with_digests(tmp_path)

    if state == "absent":
        assert uninstall.uninstall_product(ctx, purge=purge) == NOT_INSTALLED
        service.uninstall.assert_not_called()
    elif purge:
        with pytest.raises(errors.InstallError, match="holds no payload generation") as refused:
            uninstall.uninstall_product(ctx, purge=True)
        assert "preserved" in str(refused.value)
        service.uninstall.assert_called_once_with(ctx, [])
    else:
        assert uninstall.uninstall_product(ctx) == {
            "state": "uninstalled",
            "stopped": 0,
            "command_removed": False,
        }
        service.uninstall.assert_called_once_with(ctx, [])

    service.terminate_runtime.assert_not_called()
    listeners.assert_not_called()
    assert records.status(ctx) is None
    assert listing_with_digests(tmp_path) == before


@pytest.mark.parametrize("state", STATES)
def test_uninstall_stops_at_a_command_that_it_cannot_prove(
    tmp_path: Path, state: str, *, mocker: MockerFixture
) -> None:
    """The service goes first, as with a payload; another owner's command stops the purge."""
    ctx = _control_root_in(tmp_path, state)
    command_path = _another_program_at(ctx)
    service = mocker.patch.object(uninstall, "adapter").return_value
    service.status.side_effect = ["installed", "absent"]
    purge = mocker.patch.object(uninstall.transaction_completion, "purge")
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        uninstall.uninstall_product(ctx, purge=True)

    service.uninstall.assert_called_once_with(ctx, [])
    service.terminate_runtime.assert_not_called()
    purge.assert_not_called()
    assert command_path.read_text(encoding="utf-8") == "another program"
    assert listing_with_digests(tmp_path) == before


def test_uninstall_ends_the_processes_of_a_store_that_has_no_selector(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """The store's generations name their processes; the command that names one stays unproved."""
    installed = install_context(tmp_path)
    install_payload(installed, mocker=mocker)
    ctx = control_root(tmp_path)
    generation.clear(ctx)
    service = mocker.patch.object(uninstall, "adapter").return_value
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 2
    order: list[str] = []
    service.uninstall.side_effect = lambda *_arguments: order.append("service")
    service.terminate_runtime.side_effect = lambda *_arguments, **_options: (
        order.append("processes") or 2
    )
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        uninstall.uninstall_product(ctx)

    assert order == ["service", "processes"]
    service.uninstall.assert_called_once_with(ctx, [installed.executable])
    service.terminate_runtime.assert_called_once_with(installed, timeout_seconds=5.0)
    assert listing_with_digests(tmp_path) == before
