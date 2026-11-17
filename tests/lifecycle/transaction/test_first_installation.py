"""The command record and the restoration of an installation that had no selection before it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import control_root
from tests.lifecycle.fixtures import listing_with_digests
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import recover_transaction

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context
    from openai_responses_proxy.lifecycle.transaction import execution

PHASES = ("materialized", "activated")
WAYS = ("rollback", "recovery")


def _first_installation(
    ctx: runtime_context.InstallationContext, reached: str, *, mocker: MockerFixture
) -> execution.PayloadTransaction:
    """Take the first installation of a control root to one phase after its projection."""
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    if reached == "activated":
        transaction.activate()
    return transaction


def _restore(
    ctx: runtime_context.InstallationContext, transaction: execution.PayloadTransaction, way: str
) -> None:
    """Undo the installation in its own process, or as a later `recover` does."""
    if way == "rollback":
        transaction.rollback()
    else:
        assert recover_transaction(ctx, runtime=None)["state"] == "rolled_back"


def _assert_nothing_installed(ctx: runtime_context.InstallationContext) -> None:
    assert not os.path.lexists(ctx.command)
    assert generation.read(ctx) is None
    assert records.status(ctx) is None
    assert not Path(ctx.install_dir).exists()


def test_the_command_record_of_a_first_installation_names_its_candidate(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    ctx = control_root(tmp_path)
    transaction = _first_installation(ctx, "materialized", mocker=mocker)
    rollback = records.transaction_root(ctx) / "rollback"

    stored = json.loads((rollback / command.SNAPSHOT_FILENAME).read_bytes())

    assert stored == {
        "schema_version": 2,
        "state": "absent",
        "path": ctx.command,
        "target": transaction.context.executable,
        "kind": "",
        "device": 0,
        "inode": 0,
    }
    assert Path(stored["target"]).parents[2] == generation.root(ctx)
    assert stored["target"] != inventory.installed_executable(ctx.install_dir)
    read = command.read_snapshot(
        rollback, expected_sha256=str(records.read_journal(ctx)["snapshot_sha256"])
    )
    assert read.target == transaction.context.executable
    transaction.rollback()
    _assert_nothing_installed(ctx)


@pytest.mark.parametrize("way", WAYS)
@pytest.mark.parametrize("reached", PHASES)
def test_a_first_installation_is_restored_to_an_absent_command(
    tmp_path: Path, reached: str, way: str, *, mocker: MockerFixture
) -> None:
    ctx = control_root(tmp_path)
    transaction = _first_installation(ctx, reached, mocker=mocker)
    assert os.path.lexists(ctx.command) is (reached == "activated")

    _restore(ctx, transaction, way)

    _assert_nothing_installed(ctx)


@pytest.mark.parametrize("way", WAYS)
def test_the_restoration_of_a_first_installation_requires_an_absent_command(
    tmp_path: Path, way: str, *, mocker: MockerFixture
) -> None:
    """Another owner's command, put there after the record, stops the restoration untouched."""
    ctx = control_root(tmp_path)
    transaction = _first_installation(ctx, "materialized", mocker=mocker)
    command_path = Path(ctx.command)
    command_path.parent.mkdir(parents=True)
    command_path.write_text("another program", encoding="utf-8")
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        _restore(ctx, transaction, way)

    assert listing_with_digests(tmp_path) == before
    assert records.read_journal(ctx)["state"] == "materialized"
    command_path.unlink()
    _restore(ctx, transaction, way)
    _assert_nothing_installed(ctx)


def test_recovery_refuses_an_occupied_command_before_any_native_disposal(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """The restoration is admitted first: a refusal leaves the candidate's service as it is."""
    ctx = control_root(tmp_path)
    _first_installation(ctx, "materialized", mocker=mocker)
    command_path = Path(ctx.command)
    command_path.parent.mkdir(parents=True)
    command_path.write_text("another program", encoding="utf-8")
    disposal = mocker.Mock()
    before = listing_with_digests(tmp_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        recover_transaction(ctx, runtime=None, discard_native=disposal)

    disposal.assert_not_called()
    assert listing_with_digests(tmp_path) == before


def test_the_restoration_checks_the_command_again_after_it_detached_the_candidate(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """A command that appears between the two effects is not taken for the restored absence."""
    ctx = control_root(tmp_path)
    transaction = _first_installation(ctx, "activated", mocker=mocker)
    command_path = Path(ctx.command)
    clear = generation.clear

    def clear_then_lose_the_path(selected: runtime_context.InstallationContext) -> None:
        clear(selected)
        command_path.write_text("another program", encoding="utf-8")

    mocker.patch.object(generation, "clear", clear_then_lose_the_path)

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        transaction.rollback()

    assert generation.read(ctx) is None
    assert command_path.read_text(encoding="utf-8") == "another program"
    assert records.read_journal(ctx)["state"] == "activated"


@pytest.mark.parametrize("reached", PHASES)
def test_a_first_installation_that_6_0_2_interrupted_is_recovered(
    tmp_path: Path, reached: str, *, mocker: MockerFixture
) -> None:
    """6.0.2 recorded the root-level path as the target of an absent command.

    The record is read as that release wrote it, and its target is never used: the state of
    the record is `absent`.
    """
    ctx = control_root(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    rollback = records.transaction_root(ctx) / "rollback"
    written_by_6_0_2 = command.Snapshot(
        state="absent",
        path=ctx.command,
        target=inventory.installed_executable(ctx.install_dir),
    )
    command.write_snapshot(rollback, written_by_6_0_2)
    journal = records.read_journal(ctx)
    journal["snapshot_sha256"] = hashlib.sha256(
        command.snapshot_bytes(written_by_6_0_2)
    ).hexdigest()
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))
    if reached == "activated":
        transaction.activate()
        assert Path(ctx.command).samefile(transaction.context.executable)

    assert recover_transaction(ctx, runtime=None)["state"] == "rolled_back"

    _assert_nothing_installed(ctx)
    assert not Path(written_by_6_0_2.target).exists()
