"""Terminal projection and supervision binding retain recovery authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
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
    assert Path(records.transaction_root(ctx)).is_dir()
    transaction_status = records.status(ctx)
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
    assert Path(records.transaction_root(ctx)).is_dir()
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
    assert not Path(records.transaction_root(ctx)).exists()
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

    assert Path(records.transaction_root(ctx)).is_dir()
    assert Path(candidate.context.payload_dir).is_dir()
    assert payload_generation.selected_context(ctx) == predecessor.context
    assert select.call_count == 1
    bind_terminal.reset_mock(side_effect=True)
    bind_terminal.side_effect = None

    result = recover_transaction(ctx, runtime=runtime, bind_terminal=bind_terminal)

    assert result["state"] == "rolled_back"
    bind_terminal.assert_called_once_with(predecessor.context)
    assert select.call_count == 1
    assert not Path(records.transaction_root(ctx)).exists()
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
            records,
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
    assert records.journal_path(ctx).is_file()
    binder.assert_not_called()
    mocker.stop(failure)

    def bind_restored_terminal(active: runtime_context.RuntimeContext) -> None:
        assert command.status(Path(ctx.command), Path(active.executable))["state"] == "owned"

    result = recover_transaction(ctx, runtime=runtime, bind_terminal=bind_restored_terminal)

    assert result["state"] == "rolled_back"
    assert (
        command.status(Path(ctx.command), Path(predecessor.context.executable))["state"] == "owned"
    )
    assert records.status(ctx) is None
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
    assert not Path(records.transaction_root(ctx)).exists()


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
    assert not records.transaction_root(ctx).exists()
