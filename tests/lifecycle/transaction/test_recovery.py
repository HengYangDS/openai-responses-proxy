"""Payload transaction recovery behavior and ownership contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import transaction as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _retained_carrier_snapshot
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


def test_recovery_is_idempotent_when_no_transaction_exists(tmp_path: Path) -> None:
    ctx = install_context(tmp_path)

    assert recover_transaction(ctx, runtime=None) == {"state": "not_required"}


def test_fresh_recovery_removes_native_candidate_before_discarding_payload(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    journal = payload_state.journal_path(ctx).read_bytes()
    payload = Path(candidate.context.executable).read_bytes()

    def remove_native(selected: runtime_context.RuntimeContext) -> None:
        assert selected == candidate.context
        assert payload_state.journal_path(ctx).read_bytes() == journal
        assert Path(selected.executable).read_bytes() == payload

    removal = mocker.Mock(side_effect=remove_native)
    result = payload_transaction.recover(
        ctx, runtime=None, bind_terminal=mocker.Mock(), discard_native=removal
    )
    assert result["state"] == "rolled_back"
    removal.assert_called_once_with(candidate.context)
    assert not payload_state.journal_path(ctx).exists()
    assert not Path(candidate.context.payload_dir).exists()


def test_fresh_recovery_retains_payload_when_native_removal_is_unproved(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    journal = payload_state.journal_path(ctx).read_bytes()
    payload = Path(candidate.context.executable).read_bytes()
    removal = mocker.Mock(side_effect=errors.RecoveryStateError("native cleanup is unproved"))

    with pytest.raises(errors.RecoveryStateError, match="native cleanup is unproved"):
        payload_transaction.recover(
            ctx, runtime=None, bind_terminal=mocker.Mock(), discard_native=removal
        )
    assert payload_state.journal_path(ctx).read_bytes() == journal
    assert Path(candidate.context.executable).read_bytes() == payload
    assert payload_generation.selected_context(ctx) == candidate.context


@pytest.mark.parametrize("drift", ["selector", "snapshot", "command"])
def test_fresh_recovery_admits_projection_before_native_disposal(
    tmp_path: Path, drift: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    if drift == "selector":
        payload_generation.path(ctx, "b" * 32).mkdir()
        owned_files.write_bytes(
            payload_generation.selector_path(ctx),
            payload_digest.canonical_json(
                {
                    "schema_version": payload_generation.SELECTOR_SCHEMA,
                    "active": "b" * 32,
                    "predecessor": None,
                }
            ),
            mode=0o600,
        )
        message = "selection changed"
    elif drift == "snapshot":
        snapshot = payload_state.transaction_root(ctx) / "rollback" / command.SNAPSHOT_FILENAME
        snapshot.write_bytes(b"{}")
        message = "command snapshot"
    else:
        Path(ctx.command).unlink()
        Path(ctx.command).write_bytes(b"another owner")
        message = "command path changed ownership"
    before = _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused")
    command_before = Path(ctx.command).read_bytes()
    removal = mocker.Mock()
    binding = mocker.Mock()

    with pytest.raises(errors.InstallError, match=message):
        payload_transaction.recover(
            ctx, runtime=None, bind_terminal=binding, discard_native=removal
        )

    removal.assert_not_called()
    binding.assert_not_called()
    assert _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused") == before
    assert Path(ctx.command).read_bytes() == command_before


def test_recovery_finalization_selects_the_displaced_predecessor(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    projected = listener_identity.committed_payload(Path(successor.context.executable))
    assert projected is not None

    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(projected, projected),
    )

    assert result["state"] == "finalized"
    retained = payload_rollback.load_retained(ctx)
    assert retained.predecessor.release == "1.2.2"
    assert retained.successor.release == "1.2.3"


def test_interrupted_reverse_transition_recovers_the_original_successor(
    tmp_path: Path, *, mocker
) -> None:
    """Recovery reverses an activated rollback without inventing another payload copy."""
    ctx = install_context(tmp_path)
    _first = install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    original_selection = payload_generation.read(ctx)
    assert original_selection is not None
    retained = payload_rollback.load_retained(ctx)
    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    assert reverse.expected["release"] == "1.2.2"
    reverse.commit_projection()
    reverse.activate()
    reverse.preserve_for_recovery("rollback controller outcome unknown")
    original_successor = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, original_selection.active).executable)
    )
    assert original_successor is not None

    result = recover_transaction(ctx, runtime=recovery_runtime(original_successor))

    assert result["state"] == "rolled_back"
    assert payload_generation.read(ctx) == original_selection


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


def test_unselected_reverse_recovery_does_not_require_an_unused_command_snapshot(
    tmp_path: Path, *, mocker
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
    rollback = Path(payload_state.transaction_root(ctx), "rollback")
    Path(rollback, command.SNAPSHOT_FILENAME).unlink()
    rollback.rmdir()
    installed_before = Path(payload_state.installed_path(ctx)).read_bytes()
    command_before = Path(ctx.command).resolve(strict=True)
    terminal = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, selection.active).executable)
    )
    assert terminal is not None
    bind_terminal = mocker.Mock()

    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(terminal),
        bind_terminal=bind_terminal,
    )

    assert result == {
        "transaction_id": selection.predecessor,
        "version": "1.2.2",
        "state": "rolled_back",
    }
    assert payload_generation.read(ctx) == selection
    assert Path(payload_state.installed_path(ctx)).read_bytes() == installed_before
    assert Path(ctx.command).resolve(strict=True) == command_before
    assert not Path(payload_state.transaction_root(ctx)).exists()
    bind_terminal.assert_not_called()


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
    rollback = Path(payload_state.transaction_root(ctx), "rollback")
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
        installed = json.loads(Path(payload_state.installed_path(ctx)).read_text(encoding="utf-8"))
        installed["receipt_sha256"] = "0" * 64
        owned_files.write_bytes(
            payload_state.installed_path(ctx),
            payload_digest.canonical_json(installed),
            mode=0o600,
        )
    elif drift == "command":
        Path(ctx.command).unlink()
    elif drift == "command-record":
        alternate_command = tmp_path / "bin" / "alternate-proxy"
        alternate_command.parent.mkdir()
        alternate_command.symlink_to(terminal_path)
        installed = json.loads(Path(payload_state.installed_path(ctx)).read_text(encoding="utf-8"))
        installed["command"] = str(alternate_command)
        owned_files.write_bytes(
            payload_state.installed_path(ctx),
            payload_digest.canonical_json(installed),
            mode=0o600,
        )
    elif drift == "payload":
        terminal_path.write_bytes(b"tampered")
    else:
        runtime["release"] = "9.9.9"

    transaction_root = Path(payload_state.transaction_root(ctx))
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
    root = Path(payload_state.transaction_root(ctx))
    journal = Path(payload_state.journal_path(ctx))
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
                        "schema_version": payload_state.TRANSACTION_JOURNAL_SCHEMA,
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
    journal_path = Path(payload_state.journal_path(ctx))
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


def test_fresh_recovery_rejects_an_unrelated_live_runtime(tmp_path: Path, *, mocker) -> None:
    """A fresh interrupted projection cannot erase an unrelated accepting runtime."""
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    candidate.preserve_for_recovery("runtime identity is unresolved")
    projected = listener_identity.committed_payload(Path(candidate.context.executable))
    assert projected is not None
    unrelated = recovery_runtime(projected)
    unrelated["release"] = "9.9.9"

    with pytest.raises(errors.InstallError, match="does not match the candidate"):
        recover_transaction(ctx, runtime=unrelated)


def test_recovery_closes_an_unmutated_prepared_transaction(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    before = Path(ctx.executable).read_bytes()
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)

    result = recover_transaction(ctx, runtime=None)

    assert result == {
        "state": "closed",
        "transaction_id": candidate.expected["transaction_id"],
        "version": "1.2.3",
    }
    assert Path(ctx.executable).read_bytes() == before
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_recovery_refuses_a_prepared_transaction_with_unowned_content(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    residue = Path(payload_state.transaction_root(ctx), "unexpected")
    residue.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(errors.RecoveryStateError, match="unowned content"):
        recover_transaction(ctx, runtime=None)

    assert residue.is_file()
    assert Path(payload_state.journal_path(ctx)).is_file()


def test_activated_generation_recovery_restores_the_exact_prior_selection(
    tmp_path: Path, *, mocker
) -> None:
    """Forward recovery reverses to the prior active and predecessor generations."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    middle = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    middle.commit_projection()
    middle.activate()
    middle.finalize({"pid": 2})
    before = payload_generation.read(ctx)
    assert before is not None
    candidate = begin_transaction(ctx, released_artifact("1.2.4"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    candidate.preserve_for_recovery("controller outcome unknown")
    previous = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, before.active).executable)
    )
    assert previous is not None

    result = recover_transaction(ctx, runtime=recovery_runtime(previous))

    assert result["state"] == "rolled_back"
    assert payload_generation.read(ctx) == before
    assert not Path(candidate.context.payload_dir).exists()
    assert Path(ctx.command).samefile(payload_generation.context(ctx, before.active).executable)


def test_recovery_finishes_activation_when_selection_precedes_command_projection(
    tmp_path: Path, *, mocker
) -> None:
    """Recover the durable selected generation when command projection is interrupted."""
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    old_target = Path(predecessor.context.executable)
    expected_target = Path(successor.context.executable)
    interrupted = mocker.patch.object(
        payload_transaction.command,
        "project",
        side_effect=errors.InstallError("command projection interrupted"),
    )
    with pytest.raises(errors.InstallError, match="command projection interrupted"):
        successor.activate()
    mocker.stop(interrupted)

    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.active == str(successor.expected["transaction_id"])
    assert Path(ctx.command).samefile(old_target)

    candidate = listener_identity.committed_payload(Path(successor.context.executable))
    assert candidate is not None
    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(candidate, candidate),
    )

    assert result["state"] == "finalized"
    assert Path(ctx.command).samefile(expected_target)


def test_recovery_rolls_back_when_selection_precedes_the_candidate_runtime(
    tmp_path: Path, *, mocker
) -> None:
    """A selected candidate is reversible while the predecessor still serves."""
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    before = payload_generation.read(ctx)
    assert before is not None
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    interrupted = mocker.patch.object(
        payload_transaction.command,
        "project",
        side_effect=errors.InstallError("command projection interrupted"),
    )
    with pytest.raises(errors.InstallError, match="command projection interrupted"):
        successor.activate()
    mocker.stop(interrupted)
    previous = listener_identity.committed_payload(Path(predecessor.context.executable))
    assert previous is not None

    result = recover_transaction(ctx, runtime=recovery_runtime(previous))

    assert result["state"] == "rolled_back"
    assert payload_generation.read(ctx) == before
    assert Path(ctx.command).samefile(predecessor.context.executable)
    assert not Path(successor.context.payload_dir).exists()


def test_fresh_recovery_rolls_back_a_selected_candidate_without_a_runtime(
    tmp_path: Path, *, mocker
) -> None:
    """A first-install selector alone does not prove an installed runtime."""
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    interrupted = mocker.patch.object(
        payload_transaction.command,
        "project",
        side_effect=errors.InstallError("command projection interrupted"),
    )
    with pytest.raises(errors.InstallError, match="command projection interrupted"):
        candidate.activate()
    mocker.stop(interrupted)

    result = recover_transaction(ctx, runtime=None)

    assert result["state"] == "rolled_back"
    assert payload_generation.read(ctx) is None
    assert not Path(ctx.install_dir).exists()
    assert not Path(ctx.command).exists()


def test_recovery_finishes_activation_when_command_precedes_phase_journal(
    tmp_path: Path, *, mocker
) -> None:
    """Recognize durable selector and command state even if the phase journal lags."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    expected_target = Path(successor.context.executable).resolve()
    original_write = payload_transaction.state.write_journal

    def interrupt_activated_journal(*args, **kwargs):
        if kwargs.get("state") == "activated":
            raise errors.InstallError("activation journal interrupted")
        return original_write(*args, **kwargs)

    mocker.patch.object(
        payload_transaction.state,
        "write_journal",
        side_effect=interrupt_activated_journal,
    )
    with pytest.raises(errors.InstallError, match="activation journal interrupted"):
        successor.activate()

    assert Path(ctx.command).samefile(expected_target)
    assert payload_state.read_journal(ctx)["state"] == "materialized"

    candidate = listener_identity.committed_payload(Path(successor.context.executable))
    assert candidate is not None
    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(candidate, candidate),
    )

    assert result["state"] == "finalized"
    assert Path(ctx.command).samefile(expected_target)


def test_recovery_finishes_activation_when_selector_precedes_command_and_phase(
    tmp_path: Path, *, mocker
) -> None:
    """Reconcile a selector-only activation after process interruption."""
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    old_target = Path(predecessor.context.executable).resolve()
    expected_target = Path(successor.context.executable).resolve()
    interrupted = mocker.patch.object(
        payload_transaction.command,
        "project",
        side_effect=errors.InstallError("command projection interrupted"),
    )
    with pytest.raises(errors.InstallError, match="command projection interrupted"):
        successor.activate()
    mocker.stop(interrupted)

    assert Path(ctx.command).samefile(old_target)
    assert payload_state.read_journal(ctx)["state"] == "materialized"

    candidate = listener_identity.committed_payload(Path(successor.context.executable))
    assert candidate is not None
    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(candidate, candidate),
    )

    assert result["state"] == "finalized"
    assert Path(ctx.command).samefile(expected_target)


def test_recovery_rollback_restores_previous_projection_and_removes_hold(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    previous = Path(ctx.executable).read_bytes()
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.preserve_for_recovery("handoff outcome unknown")
    selection = payload_generation.read(ctx)
    assert selection is not None
    previous_identity = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, selection.active).executable)
    )
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert previous_identity is not None
    assert candidate_identity is not None
    runtime = recovery_runtime(previous_identity)

    result = recover_transaction(ctx, runtime=runtime)

    assert result["state"] == "rolled_back"
    assert Path(ctx.executable).read_bytes() == previous
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_recovery_rolls_back_an_interrupted_committed_upgrade(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    previous = Path(ctx.executable).read_bytes()
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    selection = payload_generation.read(ctx)
    assert selection is not None
    previous_identity = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, selection.active).executable)
    )
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert previous_identity is not None
    assert candidate_identity is not None
    runtime = recovery_runtime(previous_identity, candidate_identity)

    result = recover_transaction(ctx, runtime=runtime)

    assert result["state"] == "rolled_back"
    assert Path(ctx.executable).read_bytes() == previous
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_recovery_finalizes_a_verified_successor_after_controller_loss(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.preserve_for_recovery("controller outcome unknown")
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert candidate_identity is not None
    runtime = recovery_runtime(candidate_identity, candidate_identity)

    result = recover_transaction(ctx, runtime=runtime)

    assert result == {
        "transaction_id": result["transaction_id"],
        "version": "1.2.3",
        "state": "finalized",
    }
    installed = payload_state.read_installed(ctx)
    assert installed is not None
    assert installed["transaction_id"] == result["transaction_id"]
    assert installed["version"] == "1.2.3"
    assert installed["runtime"] == runtime
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_recovery_retains_finalization_authority_until_supervision_is_bound(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.preserve_for_recovery("controller outcome unknown")
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert candidate_identity is not None
    runtime = recovery_runtime(candidate_identity, candidate_identity)
    bind_terminal = mocker.Mock(side_effect=errors.InstallError("bind failed"))

    with pytest.raises(errors.InstallError, match="bind failed"):
        recover_transaction(
            ctx,
            runtime=runtime,
            bind_terminal=bind_terminal,
        )

    bind_terminal.assert_called_once_with(candidate.context)
    assert Path(payload_state.transaction_root(ctx)).is_dir()
    transaction_status = payload_state.status(ctx)
    assert transaction_status is not None
    assert transaction_status["state"] == "recovery_required"


def test_recovery_retains_rollback_authority_until_supervision_is_bound(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.preserve_for_recovery("handoff outcome unknown")
    predecessor_identity = listener_identity.committed_payload(Path(predecessor.context.executable))
    assert predecessor_identity is not None
    bind_terminal = mocker.Mock(side_effect=errors.InstallError("bind failed"))

    with pytest.raises(errors.InstallError, match="bind failed"):
        recover_transaction(
            ctx,
            runtime=recovery_runtime(predecessor_identity),
            bind_terminal=bind_terminal,
        )

    bind_terminal.assert_called_once_with(predecessor.context)
    assert Path(payload_state.transaction_root(ctx)).is_dir()
    assert Path(candidate.context.payload_dir).is_dir()
    assert payload_generation.selected_context(ctx) == predecessor.context

    bind_terminal.reset_mock(side_effect=True)
    bind_terminal.side_effect = None
    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(predecessor_identity),
        bind_terminal=bind_terminal,
    )

    assert result["state"] == "rolled_back"
    bind_terminal.assert_called_once_with(predecessor.context)
    assert not Path(payload_state.transaction_root(ctx)).exists()
    assert not Path(candidate.context.payload_dir).exists()


@pytest.mark.parametrize("reverse", [False, True], ids=["upgrade", "retained-rollback"])
def test_activated_rollback_retries_terminal_binding_without_replaying_handoff(
    tmp_path: Path, reverse: bool, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    if reverse:
        candidate.finalize({"pid": 2})
        predecessor = candidate
        candidate = payload_transaction.begin_rollback_transaction(
            ctx, payload_rollback.load_retained(ctx)
        )
        candidate.commit_projection()
        candidate.activate()
    candidate.preserve_for_recovery("handoff outcome unknown")
    predecessor_identity = listener_identity.committed_payload(Path(predecessor.context.executable))
    assert predecessor_identity is not None
    runtime = recovery_runtime(predecessor_identity)
    bind_terminal = mocker.Mock(side_effect=errors.InstallError("bind failed"))
    select = mocker.spy(payload_generation, "select")

    with pytest.raises(errors.InstallError, match="bind failed"):
        recover_transaction(ctx, runtime=runtime, bind_terminal=bind_terminal)

    assert Path(payload_state.transaction_root(ctx)).is_dir()
    assert Path(candidate.context.payload_dir).is_dir()
    assert payload_generation.selected_context(ctx) == predecessor.context
    assert select.call_count == 1
    bind_terminal.reset_mock(side_effect=True)
    bind_terminal.side_effect = None

    result = recover_transaction(ctx, runtime=runtime, bind_terminal=bind_terminal)

    assert result["state"] == "rolled_back"
    bind_terminal.assert_called_once_with(predecessor.context)
    assert select.call_count == 1
    assert not Path(payload_state.transaction_root(ctx)).exists()
    assert Path(candidate.context.payload_dir).exists() is reverse


@pytest.mark.parametrize("reverse", [False, True], ids=["upgrade", "retained-rollback"])
@pytest.mark.parametrize("windows", [False, True], ids=["posix-link", "windows-launcher"])
@pytest.mark.parametrize("phase", ["materialized", "activated"])
def test_recovery_retries_command_restoration_after_selection_is_restored(
    tmp_path: Path, reverse: bool, windows: bool, phase: str, *, mocker
) -> None:
    ctx = install_context(tmp_path, windows=windows)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3", windows=windows), mocker=mocker)
    candidate.commit_projection()
    if reverse:
        candidate.activate()
        candidate.finalize({"pid": 2})
        predecessor = candidate
        candidate = payload_transaction.begin_rollback_transaction(
            ctx, payload_rollback.load_retained(ctx)
        )
        candidate.commit_projection()
    if phase == "materialized":
        interruption = mocker.patch.object(
            payload_state,
            "write_journal",
            side_effect=errors.InstallError("activation interrupted"),
        )
        with pytest.raises(errors.InstallError, match="activation interrupted"):
            candidate.activate()
        mocker.stop(interruption)
    else:
        candidate.activate()
    prior = listener_identity.committed_payload(Path(predecessor.context.executable))
    assert prior is not None
    runtime = recovery_runtime(prior)
    failure = mocker.patch.object(
        command, "restore", side_effect=errors.InstallError("command restoration interrupted")
    )
    binder = mocker.Mock()

    with pytest.raises(errors.InstallError, match="command restoration interrupted"):
        recover_transaction(ctx, runtime=runtime, bind_terminal=binder)

    assert payload_generation.selected_context(ctx) == predecessor.context
    assert not Path(ctx.command).exists()
    assert payload_state.journal_path(ctx).is_file()
    binder.assert_not_called()
    mocker.stop(failure)

    def bind_restored_terminal(active: runtime_context.RuntimeContext) -> None:
        assert command.status(Path(ctx.command), Path(active.executable))["state"] == "owned"

    result = recover_transaction(ctx, runtime=runtime, bind_terminal=bind_restored_terminal)

    assert result["state"] == "rolled_back"
    assert (
        command.status(Path(ctx.command), Path(predecessor.context.executable))["state"] == "owned"
    )
    assert payload_state.status(ctx) is None
    assert Path(candidate.context.payload_dir).exists() is reverse


def test_recovery_binds_only_the_terminal_generation_once(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    predecessor = install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.preserve_for_recovery("handoff outcome unknown")
    predecessor_identity = listener_identity.committed_payload(Path(predecessor.context.executable))
    assert predecessor_identity is not None
    bind_terminal = mocker.Mock()

    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(predecessor_identity),
        bind_terminal=bind_terminal,
    )

    assert result["state"] == "rolled_back"
    bind_terminal.assert_called_once_with(predecessor.context)
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_recovery_keeps_superseded_generation_until_terminal_binding(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    first = install_payload(ctx, "1.2.1", mocker=mocker)
    second = begin_transaction(ctx, released_artifact("1.2.2"), mocker=mocker)
    second.commit_projection()
    second.activate()
    second.finalize({"pid": 2})
    third = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    third.commit_projection()
    third.preserve_for_recovery("controller outcome unknown")
    third_identity = listener_identity.committed_payload(Path(third.context.executable))
    assert third_identity is not None
    first_root = Path(first.context.payload_dir)

    def bind_terminal(active: runtime_context.RuntimeContext) -> None:
        assert active == third.context
        assert first_root.is_dir()

    result = recover_transaction(
        ctx,
        runtime=recovery_runtime(third_identity, third_identity),
        bind_terminal=bind_terminal,
    )

    assert result["state"] == "finalized"
    assert not first_root.exists()


def test_recovery_no_op_and_prepared_close_do_not_bind_supervision(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    bind_terminal = mocker.Mock()

    assert recover_transaction(
        ctx,
        runtime=None,
        bind_terminal=bind_terminal,
    ) == {"state": "not_required"}
    begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    assert (
        recover_transaction(
            ctx,
            runtime=None,
            bind_terminal=bind_terminal,
        )["state"]
        == "closed"
    )

    bind_terminal.assert_not_called()
    assert not payload_state.transaction_root(ctx).exists()


def test_recovery_finalizes_an_activated_fresh_candidate(tmp_path: Path, *, mocker) -> None:
    """An activated first install can finish after its runtime proves the candidate."""
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    candidate.preserve_for_recovery("controller outcome unknown")
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert candidate_identity is not None

    result = recover_transaction(ctx, runtime=recovery_runtime(candidate_identity))

    assert result["state"] == "finalized"
    assert payload_generation.read(ctx) == payload_generation.Selection(
        str(candidate.expected["transaction_id"]),
        None,
    )
    assert Path(ctx.command).samefile(candidate.context.executable)


def test_recovery_removes_an_interrupted_fresh_projection_without_a_runtime(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()

    result = recover_transaction(ctx, runtime=None)

    assert result["state"] == "rolled_back"
    assert not Path(ctx.install_dir).exists()
    assert not Path(ctx.command).exists()
    assert not Path(payload_state.transaction_root(ctx)).exists()
