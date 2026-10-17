"""Recovery refuses state, identity and ownership drift without mutation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _retained_carrier_snapshot
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


def test_interrupted_reverse_transition_rejects_the_wrong_runtime(
    tmp_path: Path, *, mocker
) -> None:
    """Reverse recovery cannot select a successor not proven by the live runtime."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    reverse.commit_projection()
    reverse.activate()
    reverse.preserve_for_recovery("rollback controller outcome unknown")

    wrong_runtime = recovery_runtime(retained.successor)
    wrong_runtime["release"] = "9.9.9"
    with pytest.raises(errors.RecoveryStateError, match="prior selected generation"):
        recover_transaction(ctx, runtime=wrong_runtime)


def test_reverse_recovery_preserves_selection_outside_its_transaction(
    tmp_path: Path, *, mocker
) -> None:
    """A reverse recovery owns only its exact before and after selection."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    reverse.commit_projection()
    reverse.activate()
    reverse.preserve_for_recovery("rollback controller outcome unknown")
    payload_generation.select(ctx, active=str(reverse.expected["transaction_id"]), predecessor=None)
    selection = payload_generation.read(ctx)
    before = _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused")
    binder = mocker.Mock()

    with pytest.raises(errors.RecoveryStateError, match="selection changed"):
        recover_transaction(ctx, runtime=recovery_runtime(retained.successor), bind_terminal=binder)

    assert payload_generation.read(ctx) == selection
    assert _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused") == before
    binder.assert_not_called()


@pytest.mark.parametrize("reverse", [False, True], ids=["upgrade", "retained-rollback"])
def test_controller_rollback_preserves_selection_outside_its_transaction(
    tmp_path: Path, reverse: bool, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    if reverse:
        candidate.finalize({"pid": 2})
        candidate = payload_transaction.begin_rollback_transaction(
            ctx, payload_rollback.load_retained(ctx)
        )
        candidate.commit_projection()
        candidate.activate()
    payload_generation.select(
        ctx, active=str(candidate.expected["transaction_id"]), predecessor=None
    )
    before = _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused")

    with pytest.raises(errors.InstallError, match="selection"):
        candidate.rollback()

    assert _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused") == before


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("selection", "selection changed"),
        ("installed", "installed state"),
        ("command", "command"),
        ("command-record", "command"),
        ("payload", "generation identity"),
        ("runtime", "runtime"),
    ],
)
def test_unselected_reverse_recovery_preserves_ambiguous_state(
    tmp_path: Path,
    drift: str,
    message: str,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    selection = payload_generation.read(ctx)
    assert selection is not None
    retained = payload_rollback.load_retained(ctx)
    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    reverse.commit_projection()
    reverse.preserve_for_recovery("rollback controller outcome unknown")
    rollback = Path(records.transaction_root(ctx), "rollback")
    Path(rollback, command.SNAPSHOT_FILENAME).unlink()
    rollback.rmdir()
    terminal_path = Path(payload_generation.context(ctx, selection.active).executable)
    terminal = listener_identity.committed_payload(terminal_path)
    assert terminal is not None
    runtime = recovery_runtime(terminal)

    if drift == "selection":
        payload_generation.select(
            ctx,
            active=selection.active,
            predecessor=None,
        )
    elif drift == "installed":
        installed = json.loads(Path(records.installed_path(ctx)).read_text(encoding="utf-8"))
        installed["receipt_sha256"] = "0" * 64
        owned_files.write_bytes(
            records.installed_path(ctx),
            payload_digest.canonical_json(installed),
            mode=0o600,
        )
    elif drift == "command":
        Path(ctx.command).unlink()
    elif drift == "command-record":
        alternate_command = tmp_path / "bin" / "alternate-proxy"
        alternate_command.parent.mkdir()
        alternate_command.symlink_to(terminal_path)
        installed = json.loads(Path(records.installed_path(ctx)).read_text(encoding="utf-8"))
        installed["command"] = str(alternate_command)
        owned_files.write_bytes(
            records.installed_path(ctx),
            payload_digest.canonical_json(installed),
            mode=0o600,
        )
    elif drift == "payload":
        terminal_path.write_bytes(b"tampered")
    else:
        runtime["release"] = "9.9.9"

    transaction_root = Path(records.transaction_root(ctx))
    with pytest.raises(errors.RecoveryStateError, match=message):
        recover_transaction(ctx, runtime=runtime)

    assert transaction_root.is_dir()


@pytest.mark.parametrize(
    ("carrier", "expected"),
    [
        ("root_symlink", "payload transaction root is a symbolic link"),
        ("root_regular_file", "payload transaction root is not a directory"),
        ("journal_missing", "payload transaction journal is missing"),
        ("journal_symlink", "payload transaction journal is a symbolic link"),
        ("journal_directory", "payload transaction journal is not a regular file"),
        ("journal_unreadable", "payload transaction journal could not be read"),
        ("journal_invalid_utf8", "payload transaction journal is malformed JSON"),
        ("journal_malformed", "payload transaction journal is malformed JSON"),
        (
            "journal_noncanonical",
            "payload transaction journal is not canonical JSON",
        ),
        (
            "journal_unsupported_schema",
            "payload transaction journal schema is unsupported",
        ),
        (
            "journal_invalid_fields",
            "payload transaction journal fields are invalid",
        ),
    ],
)
def test_recovery_classifies_invalid_carriers_without_mutation(
    tmp_path: Path,
    carrier: str,
    expected: str,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    root = Path(records.transaction_root(ctx))
    journal = Path(records.journal_path(ctx))
    target = tmp_path / "retained-target"
    root.parent.mkdir(parents=True, exist_ok=True)
    if carrier == "root_symlink":
        target.mkdir()
        (target / "evidence").write_bytes(b"retained\n")
        root.symlink_to(target, target_is_directory=True)
    elif carrier == "root_regular_file":
        root.write_bytes(b"retained root\n")
    else:
        root.mkdir(parents=True)
        if carrier == "journal_symlink":
            target.write_bytes(b"retained journal\n")
            journal.symlink_to(target)
        elif carrier == "journal_directory":
            journal.mkdir()
            (journal / "evidence").write_bytes(b"retained\n")
        elif carrier == "journal_unreadable":
            journal.write_bytes(b"retained unreadable journal\n")
        elif carrier == "journal_invalid_utf8":
            journal.write_bytes(b"\xff\xfe\x00")
        elif carrier == "journal_malformed":
            journal.write_bytes(b"{not-json\n")
        elif carrier == "journal_noncanonical":
            journal.write_bytes(
                b'{"schema_version": 1, "state": "prepared", "transaction_id": "tx", '
                b'"version": "1.2.3", "receipt_sha256": "' + b"0" * 64 + b'", "fresh": true}\n'
            )
        elif carrier == "journal_unsupported_schema":
            journal.write_bytes(
                payload_digest.canonical_json(
                    {
                        "schema_version": 2,
                        "state": "prepared",
                        "transaction_id": "tx",
                        "version": "1.2.3",
                        "receipt_sha256": "0" * 64,
                        "fresh": True,
                    }
                )
            )
        elif carrier == "journal_invalid_fields":
            journal.write_bytes(
                payload_digest.canonical_json(
                    {
                        "schema_version": records.TRANSACTION_JOURNAL_SCHEMA,
                        "state": "prepared",
                        "transaction_id": "",
                        "version": "1.2",
                        "receipt_sha256": "not-a-digest",
                        "fresh": True,
                    }
                )
            )

    before = _retained_carrier_snapshot(root, target)

    if carrier == "journal_unreadable":
        read_bytes = mocker.patch.object(
            Path,
            "read_bytes",
            side_effect=PermissionError("denied"),
        )
        with pytest.raises(errors.RecoveryStateError, match=expected):
            recover_transaction(ctx, runtime=None)
        mocker.stop(read_bytes)
    else:
        with pytest.raises(errors.RecoveryStateError, match=expected):
            recover_transaction(ctx, runtime=None)

    assert _retained_carrier_snapshot(root, target) == before


@pytest.mark.parametrize("corruption", ["identity", "release", "receipt"])
def test_recovery_rejects_candidate_identity_drift(
    tmp_path: Path, corruption: str, *, mocker
) -> None:
    """Recovery binds the candidate generation to the durable transaction journal."""
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    journal_path = Path(records.journal_path(ctx))
    if corruption == "identity":
        Path(candidate.context.executable).write_bytes(b"corrupt")
        expected = "candidate projection identity is invalid"
    else:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if corruption == "release":
            journal["version"] = "1.2.4"
        else:
            journal["receipt_sha256"] = "f" * 64
        journal_path.write_bytes(payload_digest.canonical_json(journal))
        expected = "candidate does not match the transaction"

    with pytest.raises(errors.RecoveryStateError, match=expected):
        recover_transaction(ctx, runtime=None)


def test_materialized_recovery_rejects_selection_drift(tmp_path: Path, *, mocker) -> None:
    """An unselected candidate may be discarded only from its exact prior selection."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    payload_generation.select(
        ctx,
        active=str(candidate.expected["transaction_id"]),
        predecessor=None,
    )

    with pytest.raises(errors.RecoveryStateError, match="selection changed"):
        recover_transaction(ctx, runtime=None)


def test_recovery_refuses_a_prepared_transaction_with_unowned_content(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    residue = Path(records.transaction_root(ctx), "unexpected")
    residue.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(errors.RecoveryStateError, match="unowned content"):
        recover_transaction(ctx, runtime=None)

    assert residue.is_file()
    assert Path(records.journal_path(ctx)).is_file()
