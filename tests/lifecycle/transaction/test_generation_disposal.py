"""Generation disposal retains exact byte authority across transaction recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _recover_without_binding


@pytest.mark.parametrize("outcome", ["rolled_back", "finalized"])
def test_terminal_recovery_finishes_partially_removed_generation(
    tmp_path: Path, outcome: str, *, mocker
) -> None:
    """Disposal does not need the already removed generation's payload identity."""
    ctx = install_context(tmp_path)
    oldest = install_payload(ctx, "1.2.1", mocker=mocker)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    discarded = oldest if outcome == "finalized" else transaction
    unlink = Path.unlink

    def interrupt_disposal(path: Path, *args, **kwargs) -> None:
        unlink(path, *args, **kwargs)
        if path == Path(discarded.context.executable):
            raise PermissionError("interrupted generation disposal")

    cleanup = mocker.patch.object(Path, "unlink", interrupt_disposal)
    finish = transaction.finalize if outcome == "finalized" else transaction.rollback
    with pytest.raises(errors.InstallError, match="purge failed"):
        finish()
    selection = payload_generation.read(ctx)
    installed = records.read_installed(ctx)
    mocker.stop(cleanup)

    _recover_without_binding(ctx, outcome)

    assert payload_generation.read(ctx) == selection
    assert records.read_installed(ctx) == installed
    assert not Path(discarded.context.payload_dir).exists()
    assert records.status(ctx) is None


@pytest.mark.parametrize("outcome", ["rolled_back", "finalized"])
@pytest.mark.parametrize("residue", ["file", "empty_directory", "symlink"])
def test_generation_disposal_preserves_unowned_content(
    tmp_path: Path, outcome: str, residue: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    oldest = install_payload(ctx, "1.2.1", mocker=mocker)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    discarded = oldest if outcome == "finalized" else transaction
    retained = Path(discarded.context.payload_dir, "operator-content")
    external = tmp_path / "external-content"
    external.write_bytes(b"operator data")
    if residue == "file":
        retained.write_bytes(b"operator data")
    elif residue == "empty_directory":
        retained.mkdir()
    else:
        retained.symlink_to(external)
    finish = transaction.finalize if outcome == "finalized" else transaction.rollback

    with pytest.raises(errors.InstallError, match=r"unknown.*content"):
        finish()

    assert retained.exists()
    assert external.read_bytes() == b"operator data"
    assert records.read_journal(ctx)["state"] == outcome
    if residue == "empty_directory":
        assert retained.is_dir()
        retained.rmdir()
    else:
        assert retained.read_bytes() == b"operator data"
        retained.unlink()

    _recover_without_binding(ctx, outcome)

    assert not Path(discarded.context.payload_dir).exists()
    assert records.status(ctx) is None


@pytest.mark.parametrize("outcome", ["rolled_back", "finalized"])
def test_generation_disposal_retry_preserves_replaced_owned_file(
    tmp_path: Path, outcome: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    oldest = install_payload(ctx, "1.2.1", mocker=mocker)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    discarded = oldest if outcome == "finalized" else transaction
    executable = Path(discarded.context.executable)
    before = executable.read_bytes()
    failure = mocker.patch.object(
        payload_projection, "purge_owned_files", side_effect=errors.InstallError("interrupted")
    )
    finish = transaction.finalize if outcome == "finalized" else transaction.rollback
    with pytest.raises(errors.InstallError, match="interrupted"):
        finish()
    mocker.stop(failure)
    journal = records.journal_path(ctx).read_bytes()
    executable.write_bytes(b"operator replacement")

    with pytest.raises(errors.InstallError, match="purge identity changed"):
        _recover_without_binding(ctx, outcome)

    assert executable.read_bytes() == b"operator replacement"
    assert records.journal_path(ctx).read_bytes() == journal
    executable.write_bytes(before)
    _recover_without_binding(ctx, outcome)
    assert not Path(discarded.context.payload_dir).exists()
    assert records.status(ctx) is None


@pytest.mark.parametrize("interruption", ["runtime", "manifest"])
def test_failed_materialization_disposes_only_predeclared_candidate_bytes(
    tmp_path: Path, interruption: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    selection = payload_generation.read(ctx)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    write = owned_files.write_bytes
    executable = Path(transaction.context.executable)
    manifest = Path(transaction.context.payload_dir, inventory.MANIFEST_FILENAME)

    def interrupt_write(target: Path, content: bytes, **kwargs) -> None:
        if interruption == "manifest" and target == manifest:
            raise OSError("interrupted manifest write")
        write(target, content, **kwargs)
        if interruption == "runtime" and target == executable:
            raise OSError("interrupted runtime write")

    mocker.patch.object(owned_files, "write_bytes", side_effect=interrupt_write)
    with pytest.raises(OSError, match="interrupted"):
        transaction.commit_projection()

    assert payload_generation.read(ctx) == selection
    assert not Path(transaction.context.payload_dir).exists()
    assert records.status(ctx) is None


@pytest.mark.parametrize("corruption", ["missing", "closed", "other_generation"])
def test_candidate_disposal_rejects_invalid_journal_authority(
    tmp_path: Path, corruption: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    original = records.journal_path(ctx).read_bytes()
    journal = records.read_journal(ctx)
    if corruption == "missing":
        journal.pop("files")
    elif corruption == "closed":
        journal["state"] = "closed"
    else:
        journal["files"] = {f"generations/{'f' * 32}/providers.toml": "0" * 64}
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))

    with pytest.raises(
        errors.InstallError, match=r"(fields|inventory|generation).*invalid|missing"
    ):
        records.read_journal(ctx)

    assert not Path(transaction.context.payload_dir).exists()
    records.journal_path(ctx).write_bytes(original)
    transaction.rollback()
