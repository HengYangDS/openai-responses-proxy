"""Payload transaction admission behavior and ownership contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import transaction as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import executable_relative
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _project_as_legacy_flat_install
from tests.lifecycle.transaction.fixtures import _retained_carrier_snapshot
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


@pytest.mark.parametrize("previous_release", [None, "1.2.2"])
def test_finalized_transaction_cannot_rollback_its_committed_payload(
    tmp_path: Path, previous_release: str | None, *, mocker
) -> None:
    """A consumed transaction cannot mutate its completed installation."""
    ctx = install_context(tmp_path)
    if previous_release is not None:
        install_payload(ctx, previous_release, mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 123})
    root = Path(ctx.install_dir)
    before = _retained_carrier_snapshot(root, Path(ctx.command))
    installed = records.installed_path(ctx).read_bytes()
    command_target = Path(ctx.command).resolve(strict=True)

    with pytest.raises(errors.InstallError, match="finalized"):
        transaction.rollback()

    assert _retained_carrier_snapshot(root, Path(ctx.command)) == before
    assert records.installed_path(ctx).read_bytes() == installed
    assert Path(ctx.command).resolve(strict=True) == command_target
    assert not records.transaction_root(ctx).exists()


def test_consumed_transaction_preserves_the_next_transaction(tmp_path: Path, *, mocker) -> None:
    """An old upgrade capability cannot close a later writer's journal."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.1", mocker=mocker)
    completed = install_payload(ctx, "1.2.2", mocker=mocker)
    pending = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    journal = records.journal_path(ctx).read_bytes()

    with pytest.raises(errors.InstallError, match="finalized"):
        completed.rollback()

    assert records.journal_path(ctx).read_bytes() == journal
    pending.rollback()


@pytest.mark.parametrize(
    "operation", ["materialize", "activate", "finalize", "rollback", "preserve"]
)
def test_recovered_transaction_cannot_mutate_the_next_transaction(
    tmp_path: Path, operation: str, *, mocker
) -> None:
    """Recovery consumes the journal authority of any surviving controller."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.1", mocker=mocker)
    completed = begin_transaction(ctx, released_artifact("1.2.2"), mocker=mocker)
    if operation == "materialize":
        assert recover_transaction(ctx, runtime=None)["state"] == "closed"
    else:
        completed.commit_projection()
        if operation != "activate":
            completed.activate()
        candidate = listener_identity.committed_payload(Path(completed.context.executable))
        assert candidate is not None
        assert recover_transaction(ctx, runtime=recovery_runtime(candidate))["state"] == "finalized"
    pending = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    journal = records.journal_path(ctx).read_bytes()
    before = _retained_carrier_snapshot(Path(ctx.install_dir), Path(ctx.command))

    actions: dict[str, Callable[[], None]] = {
        "materialize": completed.commit_projection,
        "activate": completed.activate,
        "finalize": lambda: completed.finalize({"pid": 123}),
        "rollback": completed.rollback,
        "preserve": lambda: completed.preserve_for_recovery("interrupted controller"),
    }
    with pytest.raises(errors.InstallError, match="no longer owns"):
        actions[operation]()

    assert records.journal_path(ctx).read_bytes() == journal
    assert _retained_carrier_snapshot(Path(ctx.install_dir), Path(ctx.command)) == before
    pending.rollback()


def test_transaction_status_is_absent_without_a_journal(tmp_path: Path) -> None:
    ctx = install_context(tmp_path)
    assert records.status(ctx) is None


@pytest.mark.parametrize("drift", ["installed", "selection", "root"])
def test_reverse_transition_rejects_stale_authority(tmp_path: Path, drift: str, *, mocker) -> None:
    """Rollback admission rechecks installed state, selection, and predecessor root."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    if drift == "installed":
        installed_path = Path(records.installed_path(ctx))
        installed = json.loads(installed_path.read_text(encoding="utf-8"))
        installed["version"] = "1.2.4"
        installed_path.write_bytes(payload_digest.canonical_json(installed))
        expected = "successor changed"
    elif drift == "selection":
        selection = payload_generation.read(ctx)
        assert selection is not None
        payload_generation.select(ctx, active=selection.active, predecessor=None)
        expected = "selection changed"
    else:
        retained = replace(retained, root=tmp_path / "unrelated-generation")
        expected = "predecessor changed"

    with pytest.raises(errors.InstallError, match=expected):
        payload_transaction.begin_rollback_transaction(ctx, retained)


def test_retained_predecessor_rejects_corruption_before_mutation(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    executable = retained.root / executable_relative()
    executable.write_bytes(b"corrupt")
    before = Path(ctx.executable).read_bytes()

    with pytest.raises(errors.InstallError, match="predecessor generation identity"):
        payload_rollback.load_retained(ctx)

    assert Path(ctx.executable).read_bytes() == before


def test_transaction_status_classifies_an_existing_invalid_carrier(tmp_path: Path) -> None:
    ctx = install_context(tmp_path)
    root = Path(records.transaction_root(ctx))
    root.mkdir(parents=True)

    assert records.status(ctx) == {
        "state": "invalid",
        "detail": "payload transaction journal is missing",
    }
    with pytest.raises(errors.InstallError, match="journal is missing"):
        recover_transaction(ctx, runtime=None)


def test_installed_state_rejects_a_symlink_even_when_its_target_is_absent(tmp_path: Path) -> None:
    ctx = install_context(tmp_path)
    installed = Path(records.installed_path(ctx))
    installed.parent.mkdir(parents=True)
    installed.symlink_to(tmp_path / "missing-installed-state.json")

    with pytest.raises(errors.InstallError, match="installed release state is invalid"):
        records.read_installed(ctx)


def test_replay_and_downgrade_are_rejected_before_any_live_write(
    tmp_path: Path, subtests, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    marker = Path(ctx.executable).read_bytes()

    for version, message in (("1.2.3", "replay"), ("1.2.2", "downgrade")):
        with (
            subtests.test(version=version),
            pytest.raises(errors.InstallError, match=message),
        ):
            begin_transaction(ctx, released_artifact(version), mocker=mocker)
        assert Path(ctx.executable).read_bytes() == marker
        assert not Path(records.transaction_root(ctx)).exists()


def test_empty_control_root_remains_a_fresh_install(tmp_path: Path, *, mocker) -> None:
    """An empty pre-created control directory does not invent a predecessor."""
    ctx = install_context(tmp_path)
    Path(ctx.install_dir).mkdir(parents=True)

    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    journal = records.read_journal(ctx)

    assert journal["fresh"] is True
    assert "previous_generation" not in journal
    candidate.commit_projection()
    rollback = Path(records.transaction_root(ctx), "rollback")
    assert {path.name for path in rollback.iterdir()} == {command.SNAPSHOT_FILENAME}
    candidate.rollback()
    assert not Path(ctx.install_dir).exists()


def test_upgrade_requires_a_selected_generation_before_any_write(tmp_path: Path, *, mocker) -> None:
    """Only a durable generation selector can authorize an installed predecessor."""
    ctx = install_context(tmp_path)
    installed = install_payload(ctx, "1.2.2", mocker=mocker)
    _project_as_legacy_flat_install(ctx, installed)
    root = Path(ctx.install_dir)
    before = _retained_carrier_snapshot(root, tmp_path / "unused")
    command_before = Path(ctx.command).readlink()

    with pytest.raises(errors.InstallError, match="requires a selected payload generation"):
        begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)

    assert _retained_carrier_snapshot(root, tmp_path / "unused") == before
    assert Path(ctx.command).readlink() == command_before
    assert not Path(records.transaction_root(ctx)).exists()


def test_nonempty_control_root_without_installed_authority_is_rejected(
    tmp_path: Path, *, mocker
) -> None:
    """Unowned residue cannot be interpreted as an install predecessor."""
    ctx = install_context(tmp_path)
    install_root = Path(ctx.install_dir)
    install_root.mkdir(parents=True)
    (install_root / "unknown.txt").write_text("operator data", encoding="utf-8")

    with pytest.raises(errors.InstallError, match="unverified content"):
        begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)

    assert (install_root / "unknown.txt").read_text(encoding="utf-8") == "operator data"
    assert not Path(records.transaction_root(ctx)).exists()


def test_transaction_status_projects_only_the_read_only_recovery_contract(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.preserve_for_recovery("handoff outcome unknown")
    journal_path = Path(records.journal_path(ctx))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal.update(
        {
            "authorization": "Bearer secret-token",
            "request_body": {"input": "private request"},
            "stage_path": "/private/release-stage",
            "reason": (
                "handoff unknown; Authorization=Bearer secret-token; "
                "body=private request; stage=/private/release-stage"
            ),
        }
    )
    journal_path.write_bytes(payload_digest.canonical_json(journal))
    before = journal_path.read_bytes()

    evidence = records.status(ctx)

    assert evidence == {
        "state": "invalid",
        "detail": "payload transaction journal fields are invalid",
    }
    serialized = json.dumps(evidence, sort_keys=True)
    for forbidden in ("secret-token", "private request", "/private/release-stage"):
        assert forbidden not in serialized
    assert journal_path.read_bytes() == before


def test_transaction_state_machine_rejects_invalid_order_and_duplicate_commit(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path / "transaction")
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    assert transaction.release == "1.2.3"
    assert transaction.expected["release"] == "1.2.3"
    with pytest.raises(errors.InstallError, match="not activated"):
        transaction.finalize()
    with pytest.raises(errors.InstallError, match="only a materialized"):
        transaction.preserve_for_recovery("not committed")
    transaction.commit_projection()
    with pytest.raises(errors.InstallError, match="not prepared"):
        transaction.commit_projection()
    transaction.rollback()
    transaction.rollback()
    with pytest.raises(TypeError):
        payload_transaction.PayloadTransaction(
            ctx=ctx,
            blobs=(),
            version="1.2.3",
            receipt_sha256="0" * 64,
            receipt={},
            transaction_id="test",
            fresh=True,
            previous_generation=None,
        )

    fresh = begin_transaction(
        install_context(tmp_path / "fresh"),
        released_artifact(),
        mocker=mocker,
    )
    fresh.rollback()
    payload_transaction._remove_transaction_root(fresh._ctx)
