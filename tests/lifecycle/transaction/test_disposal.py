"""Payload transaction disposal behavior and ownership contracts."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion as transaction_completion
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity as listener_identity
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _project_as_legacy_flat_install
from tests.lifecycle.transaction.fixtures import _recover_without_binding
from tests.lifecycle.transaction.fixtures import _retained_carrier_snapshot
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


def test_nonfresh_rollback_without_retained_snapshot_only_closes_transaction(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    rollback = Path(records.transaction_root(ctx), "rollback")
    rollback.mkdir()
    rollback.rmdir()

    transaction.rollback()

    assert not Path(records.transaction_root(ctx)).exists()
    transaction.rollback()


@pytest.mark.parametrize("previous_release", [None, "1.2.2"])
@pytest.mark.parametrize("phase", ["prepared", "materialized", "activated"])
def test_rollback_retries_cleanup_before_becoming_terminal(
    tmp_path: Path, previous_release: str | None, phase: str, *, mocker
) -> None:
    """A failed cleanup remains retryable and cannot consume a later journal."""
    ctx = install_context(tmp_path)
    if previous_release is not None:
        install_payload(ctx, previous_release, mocker=mocker)
    selection = payload_generation.read(ctx)
    installed = records.read_installed(ctx)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    if phase != "prepared":
        transaction.commit_projection()
    if phase == "activated":
        transaction.activate()
    cleanup = mocker.patch.object(
        transaction_completion,
        "_remove_transaction_root",
        side_effect=errors.InstallError("payload transaction cleanup failed"),
    )

    with pytest.raises(errors.InstallError, match="cleanup failed"):
        transaction.rollback()

    assert records.journal_path(ctx).is_file()
    assert payload_generation.read(ctx) == selection
    assert records.read_installed(ctx) == installed
    assert not Path(transaction.context.payload_dir).exists()
    mocker.stop(cleanup)

    transaction.rollback()

    assert not records.transaction_root(ctx).exists()
    assert payload_generation.read(ctx) == selection
    assert records.read_installed(ctx) == installed
    pending = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    journal = records.journal_path(ctx).read_bytes()
    transaction.rollback()
    assert records.journal_path(ctx).read_bytes() == journal
    pending.rollback()


@pytest.mark.parametrize("previous_release", [None, "1.2.2"])
@pytest.mark.parametrize("outcome", ["rolled_back", "finalized"])
@pytest.mark.parametrize("interruption", ["snapshot", "root", "journal"])
def test_recovery_finishes_interrupted_terminal_cleanup(
    tmp_path: Path,
    previous_release: str | None,
    outcome: str,
    interruption: str,
    *,
    mocker,
) -> None:
    """Recovery survives a removed snapshot without reapplying terminal effects."""
    ctx = install_context(tmp_path)
    if previous_release is not None:
        install_payload(ctx, previous_release, mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    projected = listener_identity.committed_payload(Path(transaction.context.executable))
    assert projected is not None
    runtime = recovery_runtime(projected, projected)
    root = records.transaction_root(ctx)
    remove_tree = transaction_completion.shutil.rmtree
    unlink = Path.unlink

    def interrupt_cleanup(path: Path) -> None:
        if Path(path) == root:
            if interruption == "snapshot":
                remove_tree(root / "rollback")
                raise PermissionError("interrupted terminal cleanup")
            if interruption == "root":
                remove_tree(root)
                raise PermissionError("interrupted terminal cleanup")
        remove_tree(path)

    def interrupt_journal(path: Path, *args, **kwargs) -> None:
        if interruption == "journal" and path == records.journal_path(ctx):
            raise PermissionError("interrupted terminal cleanup")
        unlink(path, *args, **kwargs)

    cleanup = mocker.patch.object(
        transaction_completion.shutil, "rmtree", side_effect=interrupt_cleanup
    )
    journal_cleanup = mocker.patch.object(Path, "unlink", interrupt_journal)
    finish = (
        (lambda: transaction.finalize(runtime)) if outcome == "finalized" else transaction.rollback
    )
    with pytest.raises(errors.InstallError, match="cleanup failed"):
        finish()
    selection = payload_generation.read(ctx)
    installed = records.read_installed(ctx)
    mocker.stop(cleanup)
    mocker.stop(journal_cleanup)
    journal = records.journal_path(ctx).read_bytes()
    with pytest.raises(errors.RecoveryRequiredError, match="complete payload recovery"):
        begin_transaction(ctx, released_artifact("1.2.4"), mocker=mocker)
    assert records.journal_path(ctx).read_bytes() == journal

    recovery = multiprocessing.get_context("spawn").Process(
        target=_recover_without_binding, args=(ctx, outcome)
    )
    recovery.start()
    try:
        recovery.join(timeout=30)
        assert recovery.exitcode == 0
    finally:
        if recovery.is_alive():
            recovery.kill()
            recovery.join()
        recovery.close()

    assert payload_generation.read(ctx) == selection
    assert records.read_installed(ctx) == installed
    assert not root.exists()
    assert not records.journal_path(ctx).exists()
    assert recover_transaction(ctx, runtime=None) == {"state": "not_required"}


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


def test_purge_recovery_uses_its_inventory_in_a_fresh_process(tmp_path: Path, *, mocker) -> None:
    """Disposal authority survives the controller and its selected executable."""
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    executable = Path(ctx.executable)
    unlink = Path.unlink

    def interrupt(path: Path, *args, **kwargs) -> None:
        unlink(path, *args, **kwargs)
        if path == executable:
            raise PermissionError("interrupted payload disposal")

    failure = mocker.patch.object(Path, "unlink", interrupt)
    with pytest.raises(errors.InstallError, match="purge failed"):
        transaction_completion.purge(ctx)
    mocker.stop(failure)
    assert not executable.exists()
    recovery = multiprocessing.get_context("spawn").Process(
        target=_recover_without_binding, args=(ctx, "purged")
    )
    recovery.start()
    try:
        recovery.join(timeout=30)
        assert recovery.exitcode == 0
    finally:
        if recovery.is_alive():
            recovery.kill()
            recovery.join()
        recovery.close()
    assert not Path(ctx.install_dir).exists()
    assert records.status(ctx) is None


def test_purge_accepts_a_verified_flat_payload_without_installed_state(
    tmp_path: Path, *, mocker
) -> None:
    """A preserved complete manifest proves removal without a selected generation."""
    ctx = install_context(tmp_path)
    installed = install_payload(ctx, mocker=mocker)
    _project_as_legacy_flat_install(ctx, installed)
    payload_generation.root(ctx).rmdir()
    records.installed_path(ctx).unlink()

    assert transaction_completion.purge(ctx)["state"] == "purged"
    assert not Path(ctx.install_dir).exists()
    assert records.status(ctx) is None


def test_pending_transaction_retains_exclusive_removal_authority(tmp_path: Path, *, mocker) -> None:
    """Purge cannot replace a live transaction's journal or installed selection."""
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    pending = begin_transaction(ctx, released_artifact("1.2.4"), mocker=mocker)
    before = records.journal_path(ctx).read_bytes()
    with pytest.raises(errors.RecoveryRequiredError, match="recovery"):
        transaction_completion.purge(ctx)
    assert records.journal_path(ctx).read_bytes() == before
    pending.rollback()


@pytest.mark.parametrize(
    "corruption",
    ["empty", "generation", "unknown", "digest", "state", "transaction_root"],
)
def test_removal_authority_is_validated_before_disposal(
    tmp_path: Path, corruption: str, *, mocker
) -> None:
    """Only a canonical terminal inventory grants deletion of recorded bytes."""
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    failure = mocker.patch.object(
        payload_projection, "purge_owned_files", side_effect=errors.InstallError("interrupted")
    )
    with pytest.raises(errors.InstallError, match="interrupted"):
        transaction_completion.purge(ctx)
    mocker.stop(failure)
    journal = records.read_journal(ctx)
    files = journal["files"]
    assert isinstance(files, dict)
    match corruption:
        case "empty":
            journal["files"] = {}
        case "generation":
            files["generations/not-a-generation/providers.toml"] = "0" * 64
        case "unknown":
            files["operator.txt"] = "0" * 64
        case "digest":
            files[next(iter(files))] = "invalid"
        case "state":
            journal["state"] = "closed"
        case "transaction_root":
            records.transaction_root(ctx).mkdir()
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))
    before = _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unrelated")

    with pytest.raises(errors.RecoveryStateError):
        _recover_without_binding(ctx, "purged")

    assert _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unrelated") == before


@pytest.mark.parametrize("replacement", ["file", "symlink", "unknown", "empty_directory"])
def test_purge_recovery_preserves_unowned_replacements(
    tmp_path: Path, replacement: str, *, mocker
) -> None:
    """An interrupted purge never expands its original byte or directory ownership."""
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    failure = mocker.patch.object(
        payload_projection, "purge_owned_files", side_effect=errors.InstallError("interrupted")
    )
    with pytest.raises(errors.InstallError, match="interrupted"):
        transaction_completion.purge(ctx)
    mocker.stop(failure)
    retained = Path(ctx.executable)
    expected = b"operator data"
    if replacement == "file":
        retained.write_bytes(expected)
    elif replacement == "symlink":
        external = tmp_path / "external"
        external.write_bytes(expected)
        retained.unlink()
        retained.symlink_to(external)
    else:
        retained = Path(ctx.payload_dir, "bin", "operator")
        if replacement == "unknown":
            retained.write_bytes(expected)
        else:
            retained.mkdir()

    with pytest.raises(errors.InstallError):
        _recover_without_binding(ctx, "purged")

    if replacement == "empty_directory":
        assert retained.is_dir()
    else:
        assert retained.read_bytes() == expected
    pending = records.status(ctx)
    assert pending is not None
    assert pending["state"] == "purged"


@pytest.mark.parametrize("restart", [False, True])
def test_snapshot_failure_cleanup_remains_retryable(
    tmp_path: Path, restart: bool, *, mocker
) -> None:
    """An incomplete snapshot has no rollback authority and needs only disposal."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    snapshot = mocker.patch.object(
        command, "write_snapshot", side_effect=errors.InstallError("snapshot failed")
    )
    cleanup = mocker.patch.object(
        transaction_completion.shutil, "rmtree", side_effect=PermissionError("cleanup failed")
    )
    with pytest.raises(errors.InstallError, match="cleanup failed"):
        transaction.commit_projection()
    mocker.stop(cleanup)
    mocker.stop(snapshot)

    if restart:
        _recover_without_binding(ctx, "closed")
    else:
        transaction.rollback()

    assert records.status(ctx) is None
    assert not Path(ctx.install_dir).exists()
    assert not Path(ctx.command).exists()


@pytest.mark.parametrize("operation", ["activate", "preserve"])
def test_disposal_hold_cannot_reopen_a_terminal_transaction(
    tmp_path: Path, operation: str, *, mocker
) -> None:
    """A surviving controller cannot overwrite a durable terminal outcome."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    cleanup = mocker.patch.object(
        transaction_completion.shutil, "rmtree", side_effect=PermissionError("cleanup failed")
    )
    with pytest.raises(errors.InstallError, match="transaction cleanup failed"):
        transaction.rollback()
    journal = records.journal_path(ctx).read_bytes()
    mocker.stop(cleanup)
    action = (
        transaction.activate
        if operation == "activate"
        else lambda: transaction.preserve_for_recovery("controller outcome unknown")
    )

    with pytest.raises(errors.InstallError, match="terminal"):
        action()

    assert records.journal_path(ctx).read_bytes() == journal
    transaction.rollback()


def test_recovery_cleans_a_finalized_transaction_without_rolling_back(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert candidate_identity is not None
    runtime = recovery_runtime(candidate_identity, candidate_identity)
    installed = {
        "schema_version": records.INSTALLED_RELEASE_STATE_SCHEMA,
        "version": "1.2.3",
        "receipt_sha256": candidate.receipt_sha256,
        "transaction_id": json.loads(Path(records.journal_path(ctx)).read_text(encoding="utf-8"))[
            "transaction_id"
        ],
        "command": ctx.command,
        "runtime": runtime,
    }
    Path(records.installed_path(ctx)).write_bytes(payload_digest.canonical_json(installed))
    candidate_bytes = Path(ctx.executable).read_bytes()

    result = recover_transaction(ctx, runtime=runtime)

    assert result["state"] == "finalized"
    assert Path(ctx.executable).read_bytes() == candidate_bytes
    assert not Path(records.transaction_root(ctx)).exists()


def test_finalize_reports_cleanup_failure_without_erasing_success_state(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    mocker.patch.object(
        transaction_completion.shutil,
        "rmtree",
        side_effect=OSError("cleanup blocked at /Users/private/transaction"),
    )

    with pytest.raises(errors.InstallError, match="cleanup failed") as raised:
        transaction.finalize({"pid": 123})
    assert "/Users/private/transaction" not in str(raised.value)

    assert Path(records.installed_path(ctx)).is_file()
    assert Path(records.journal_path(ctx)).is_file()


def test_finalization_journal_omits_private_failure_details(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    mocker.patch.object(
        payload_generation,
        "select",
        side_effect=errors.InstallError("selection failed at /Users/private/payload"),
    )

    with pytest.raises(errors.InstallError, match="selection failed"):
        transaction.finalize({"pid": 123})

    journal = records.read_journal(ctx)
    assert journal["state"] == "recovery_required"
    assert journal["reason"] == "finalization failed"
    assert "/Users/private/payload" not in Path(records.journal_path(ctx)).read_text()


def test_commit_and_cleanup_fail_closed_on_unproved_terminal_state(
    tmp_path: Path, subtests, *, mocker
) -> None:
    ctx = install_context(tmp_path / "integrity")
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    mocker.patch.object(
        payload_projection,
        "verify_payload_manifest",
        return_value=(False, "tampered"),
    )
    with (
        subtests.test("integrity"),
        pytest.raises(
            errors.InstallError,
            match="committed payload integrity check failed: tampered",
        ),
    ):
        transaction.commit_projection()
    mocker.stopall()

    ctx = install_context(tmp_path / "rollback")
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    mocker.patch.object(
        payload_transaction.payload_candidate,
        "write_projection",
        side_effect=OSError("write failed"),
    )
    mocker.patch.object(
        payload_transaction.PayloadTransaction,
        "rollback",
        side_effect=errors.InstallError("restore failed at /Users/private/rollback"),
    )
    with (
        subtests.test("rollback"),
        pytest.raises(
            errors.InstallError, match="payload commit failed and rollback failed"
        ) as raised,
    ):
        transaction.commit_projection()
    assert "/Users/private/rollback" not in str(raised.value)
    mocker.stopall()

    ctx = install_context(tmp_path / "cleanup")
    root = records.transaction_root(ctx)
    root.mkdir(parents=True)
    mocker.patch.object(transaction_completion.shutil, "rmtree", return_value=None)
    with (
        subtests.test("cleanup"),
        pytest.raises(errors.InstallError, match="cleanup did not remove"),
    ):
        transaction_completion._remove_transaction_root(ctx)
