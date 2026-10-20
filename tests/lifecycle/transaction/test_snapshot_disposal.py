"""Transaction snapshot disposal retains exact authority through interruption."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion as transaction_completion
from openai_responses_proxy.serving import digest as payload_digest
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _recover_without_binding


@pytest.mark.parametrize("outcome", ["rolled_back", "finalized"])
@pytest.mark.parametrize("replacement", ["bytes", "symlink"])
def test_transaction_disposal_retry_preserves_replaced_snapshot(
    tmp_path: Path, outcome: str, replacement: str, *, mocker
) -> None:
    """A terminal retry never derives deletion authority from replacement bytes."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    snapshot = records.transaction_root(ctx) / "rollback" / command.SNAPSHOT_FILENAME
    before = snapshot.read_bytes()
    failure = mocker.patch.object(
        transaction_completion,
        "_remove_transaction_root",
        side_effect=errors.InstallError("interrupted"),
    )
    finish = transaction.finalize if outcome == "finalized" else transaction.rollback
    with pytest.raises(errors.InstallError, match="interrupted"):
        finish()
    mocker.stop(failure)
    journal = records.journal_path(ctx).read_bytes()
    external = tmp_path / "external-snapshot"
    external.write_bytes(before)
    if replacement == "bytes":
        snapshot.write_bytes(b"operator replacement")
    else:
        snapshot.unlink()
        snapshot.symlink_to(external)

    with pytest.raises(errors.InstallError, match="transaction cleanup failed"):
        _recover_without_binding(ctx, outcome)

    assert records.journal_path(ctx).read_bytes() == journal
    assert external.read_bytes() == before
    if replacement == "bytes":
        assert snapshot.read_bytes() == b"operator replacement"
    else:
        assert snapshot.is_symlink()
        snapshot.unlink()
    snapshot.write_bytes(before)
    _recover_without_binding(ctx, outcome)
    assert records.status(ctx) is None


def test_transaction_disposal_refuses_a_substituted_root(tmp_path: Path) -> None:
    """A transaction-root link cannot authorize deleting an external directory."""
    ctx = install_context(tmp_path)
    external = tmp_path / "external-transaction"
    rollback = external / "rollback"
    rollback.mkdir(parents=True)
    root = records.transaction_root(ctx)
    root.parent.mkdir(parents=True)
    root.symlink_to(external, target_is_directory=True)

    with pytest.raises(errors.InstallError, match="transaction cleanup failed"):
        transaction_completion._remove_transaction_root(ctx)

    assert root.is_symlink()
    assert rollback.is_dir()


@pytest.mark.parametrize("corruption", ["missing", "empty", "wrong_type", "non_digest"])
def test_snapshot_disposal_rejects_invalid_journal_authority(
    tmp_path: Path, corruption: str, *, mocker
) -> None:
    """Materialized transactions require one well-formed original snapshot digest."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    original = records.journal_path(ctx).read_bytes()
    journal = records.read_journal(ctx)
    if corruption == "missing":
        journal.pop("snapshot_sha256")
    else:
        journal["snapshot_sha256"] = {"empty": "", "wrong_type": 42, "non_digest": "x" * 64}[
            corruption
        ]
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))

    with pytest.raises(errors.InstallError, match="journal fields are invalid"):
        records.read_journal(ctx)

    records.journal_path(ctx).write_bytes(original)
    transaction.rollback()


@pytest.mark.parametrize("outcome", ["closed", "rolled_back", "finalized"])
@pytest.mark.parametrize("location", ["root", "rollback"])
@pytest.mark.parametrize("residue", ["file", "empty_directory", "symlink"])
def test_transaction_disposal_preserves_unowned_content(
    tmp_path: Path, outcome: str, location: str, residue: str, *, mocker
) -> None:
    """Terminal disposal preserves unknown content and its recovery journal."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    root = records.transaction_root(ctx)
    if outcome != "closed":
        transaction.commit_projection()
        transaction.activate()
    directory = root if location == "root" else root / "rollback"
    directory.mkdir(exist_ok=True)
    retained = directory / "operator-content"
    external = tmp_path / "external-content"
    external.write_bytes(b"operator data")
    if residue == "file":
        retained.write_bytes(b"operator data")
    elif residue == "empty_directory":
        retained.mkdir()
    else:
        retained.symlink_to(external)

    finish = (
        (
            lambda: transaction_completion.complete_transaction(
                ctx, records.read_journal(ctx), outcome="closed"
            )
        )
        if outcome == "closed"
        else transaction.finalize
        if outcome == "finalized"
        else transaction.rollback
    )
    with pytest.raises(errors.InstallError, match=r"unknown.*content"):
        finish()

    assert retained.exists()
    assert external.read_bytes() == b"operator data"
    assert records.read_journal(ctx)["state"] == outcome
    if residue == "empty_directory":
        retained.rmdir()
    else:
        assert retained.read_bytes() == b"operator data"
        retained.unlink()

    _recover_without_binding(ctx, outcome)

    assert not root.exists()
    assert records.status(ctx) is None
