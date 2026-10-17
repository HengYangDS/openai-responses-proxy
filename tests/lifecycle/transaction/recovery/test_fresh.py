"""Fresh and prepared recovery preserves native and payload ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import recovery as transaction_recovery
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
    journal = records.journal_path(ctx).read_bytes()
    payload = Path(candidate.context.executable).read_bytes()

    def remove_native(selected: runtime_context.RuntimeContext) -> None:
        assert selected == candidate.context
        assert records.journal_path(ctx).read_bytes() == journal
        assert Path(selected.executable).read_bytes() == payload

    removal = mocker.Mock(side_effect=remove_native)
    result = transaction_recovery.recover(
        ctx, runtime=None, bind_terminal=mocker.Mock(), discard_native=removal
    )
    assert result["state"] == "rolled_back"
    removal.assert_called_once_with(candidate.context)
    assert not records.journal_path(ctx).exists()
    assert not Path(candidate.context.payload_dir).exists()


def test_fresh_recovery_retains_payload_when_native_removal_is_unproved(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    journal = records.journal_path(ctx).read_bytes()
    payload = Path(candidate.context.executable).read_bytes()
    removal = mocker.Mock(side_effect=errors.RecoveryStateError("native cleanup is unproved"))

    with pytest.raises(errors.RecoveryStateError, match="native cleanup is unproved"):
        transaction_recovery.recover(
            ctx, runtime=None, bind_terminal=mocker.Mock(), discard_native=removal
        )
    assert records.journal_path(ctx).read_bytes() == journal
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
        snapshot = records.transaction_root(ctx) / "rollback" / command.SNAPSHOT_FILENAME
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
        transaction_recovery.recover(
            ctx, runtime=None, bind_terminal=binding, discard_native=removal
        )

    removal.assert_not_called()
    binding.assert_not_called()
    assert _retained_carrier_snapshot(Path(ctx.install_dir), tmp_path / "unused") == before
    assert Path(ctx.command).read_bytes() == command_before


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
    assert not Path(records.transaction_root(ctx)).exists()


def test_fresh_recovery_rolls_back_a_selected_candidate_without_a_runtime(
    tmp_path: Path, *, mocker
) -> None:
    """A first-install selector alone does not prove an installed runtime."""
    ctx = install_context(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    interrupted = mocker.patch.object(
        command,
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
    assert not Path(records.transaction_root(ctx)).exists()
