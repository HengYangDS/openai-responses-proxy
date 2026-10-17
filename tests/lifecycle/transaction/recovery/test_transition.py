"""Interrupted forward and reverse payload transitions reconcile durable state."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


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
    rollback = Path(records.transaction_root(ctx), "rollback")
    Path(rollback, command.SNAPSHOT_FILENAME).unlink()
    rollback.rmdir()
    installed_before = Path(records.installed_path(ctx)).read_bytes()
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
    assert Path(records.installed_path(ctx)).read_bytes() == installed_before
    assert Path(ctx.command).resolve(strict=True) == command_before
    assert not Path(records.transaction_root(ctx)).exists()
    bind_terminal.assert_not_called()


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
        command,
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
        command,
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


def test_recovery_finishes_activation_when_command_precedes_phase_journal(
    tmp_path: Path, *, mocker
) -> None:
    """Recognize durable selector and command state even if the phase journal lags."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    expected_target = Path(successor.context.executable).resolve()
    original_write = records.write_journal

    def interrupt_activated_journal(*args, **kwargs):
        if kwargs.get("state") == "activated":
            raise errors.InstallError("activation journal interrupted")
        return original_write(*args, **kwargs)

    mocker.patch.object(
        records,
        "write_journal",
        side_effect=interrupt_activated_journal,
    )
    with pytest.raises(errors.InstallError, match="activation journal interrupted"):
        successor.activate()

    assert Path(ctx.command).samefile(expected_target)
    assert records.read_journal(ctx)["state"] == "materialized"

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
        command,
        "project",
        side_effect=errors.InstallError("command projection interrupted"),
    )
    with pytest.raises(errors.InstallError, match="command projection interrupted"):
        successor.activate()
    mocker.stop(interrupted)

    assert Path(ctx.command).samefile(old_target)
    assert records.read_journal(ctx)["state"] == "materialized"

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
    assert not Path(records.transaction_root(ctx)).exists()


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
    assert not Path(records.transaction_root(ctx)).exists()


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
    installed = records.read_installed(ctx)
    assert installed is not None
    assert installed["transaction_id"] == result["transaction_id"]
    assert installed["version"] == "1.2.3"
    assert installed["runtime"] == runtime
    assert not Path(records.transaction_root(ctx)).exists()
