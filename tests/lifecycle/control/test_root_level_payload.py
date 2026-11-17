"""Payload files at the control root's own level are unverified content, not an installation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import executable_relative
from tests.lifecycle.fixtures import healthy_status_dependencies
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import listing_with_digests
from tests.lifecycle.fixtures import root_level_payload

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

    from openai_responses_proxy.lifecycle import context as runtime_context


def _stopped_supervisor(ctx: runtime_context.RuntimeContext, *, mocker: MockerFixture) -> MockType:
    """Project a registered supervisor that declares the context's executable and runs nothing."""
    native = cast("MockType", mocker.patch.object(control, "adapter").return_value)
    native.status.return_value = "installed"
    native.configured_executable.return_value = ctx.executable
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control.process, "listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    return native


@pytest.mark.parametrize("supervisor", ["running", "stopped"])
def test_status_reports_no_installation_for_a_payload_at_the_control_root(
    tmp_path: Path, supervisor: str, *, mocker: MockerFixture
) -> None:
    """A manifest, a carrier and an installed record do not stand in for a selection."""
    ctx = root_level_payload(install_context(tmp_path), mocker=mocker)
    if supervisor == "running":
        healthy_status_dependencies(ctx, mocker=mocker)
    else:
        _stopped_supervisor(ctx, mocker=mocker)
    before = listing_with_digests(tmp_path)

    observed = control.status(ctx)

    assert observed["state"] == "degraded"
    assert listing_with_digests(tmp_path) == before


def test_rollback_proves_no_release_active_at_the_control_root_and_changes_no_file(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    ctx = root_level_payload(install_context(tmp_path), mocker=mocker)
    healthy_status_dependencies(ctx, mocker=mocker)
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
    """The installed record is bound to the selection by rollback itself as well."""
    ctx = root_level_payload(install_context(tmp_path), mocker=mocker)
    mocker.patch.object(control, "status", return_value={"state": "running", "release": "1.2.3"})

    with pytest.raises(errors.RollbackStateError, match="not proven active"):
        control.rollback(ctx, to_release="1.2.3")


def test_reload_starts_nothing_for_a_payload_at_the_control_root(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    ctx = root_level_payload(install_context(tmp_path), mocker=mocker)
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
    ctx = root_level_payload(install_context(tmp_path), mocker=mocker)
    before = listing_with_digests(tmp_path)
    assert {name for name, _ in listing_with_digests(Path(ctx.install_dir))} == {
        "bin",
        executable_relative(),
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
