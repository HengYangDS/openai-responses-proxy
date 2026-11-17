"""Recovery restores a transaction that reached activation only on a read that follows a wait.

A transaction is activated before its handoff request is sent, and a listener that has
received a request and not begun it shows the same session at rest as one that never will. So
a predecessor at rest restores nothing at once: recovery waits, reads health again, and judges
that read with every decision that it has for the first.

The wait and the second read are one reader here. ``tests/lifecycle/control`` holds the
clock's side of the wait, and the shared fixture fails a recovery that waits where its test
names no such reader.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import control_root
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import interrupted_transition
from tests.lifecycle.transaction.fixtures import observed_installation
from tests.lifecycle.transaction.fixtures import recover_transaction

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_NOT_AT_REST = "payload recovery runtime does not match the prior selected generation"


@pytest.mark.parametrize("preserved", [False, True], ids=["activated", "recovery_required"])
@pytest.mark.parametrize("reverse", [False, True], ids=["upgrade", "retained-rollback"])
def test_a_predecessor_at_rest_is_restored_only_on_the_read_that_follows_the_wait(
    tmp_path: Path, *, reverse: bool, preserved: bool, mocker: MockerFixture
) -> None:
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reverse=reverse, preserved=preserved)
    pending = observed_installation(ctx)
    before_the_second_read: list[object] = []

    def read_after_wait() -> dict[str, object]:
        before_the_second_read.append(observed_installation(ctx))
        return interrupted.at_rest

    bind_terminal = mocker.Mock()

    result = recover_transaction(
        ctx,
        runtime=interrupted.at_rest,
        read_after_wait=read_after_wait,
        bind_terminal=bind_terminal,
    )

    # One wait, and nothing was restored before it was over.
    assert before_the_second_read == [pending]
    assert result == {
        "transaction_id": interrupted.transaction.expected["transaction_id"],
        "version": "1.2.2" if reverse else "1.2.3",
        "state": "rolled_back",
    }
    assert payload_generation.read(ctx) == interrupted.prior
    assert Path(ctx.command).samefile(interrupted.prior_context.executable)
    assert records.status(ctx) is None
    assert not records.transaction_root(ctx).exists()
    assert Path(interrupted.transaction.context.payload_dir).exists() is reverse
    bind_terminal.assert_called_once_with(interrupted.prior_context)


@pytest.mark.parametrize("preserved", [False, True], ids=["activated", "recovery_required"])
def test_a_successor_that_committed_during_the_wait_is_finalized_and_nothing_is_restored(
    tmp_path: Path, *, preserved: bool, mocker: MockerFixture
) -> None:
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, preserved=preserved)
    candidate = interrupted.transaction
    transaction_id = str(candidate.expected["transaction_id"])
    bind_terminal = mocker.Mock()

    result = recover_transaction(
        ctx,
        runtime=interrupted.at_rest,
        read_after_wait=lambda: interrupted.committed,
        bind_terminal=bind_terminal,
    )

    assert result == {"transaction_id": transaction_id, "version": "1.2.3", "state": "finalized"}
    assert payload_generation.read(ctx) == payload_generation.Selection(
        transaction_id, interrupted.prior.active
    )
    assert Path(ctx.command).samefile(candidate.context.executable)
    installed = records.read_installed(ctx)
    assert installed is not None
    assert (installed["transaction_id"], installed["version"]) == (transaction_id, "1.2.3")
    assert installed["runtime"] == interrupted.committed
    assert Path(candidate.context.payload_dir).is_dir()
    assert Path(interrupted.prior_context.payload_dir).is_dir()
    assert records.status(ctx) is None
    bind_terminal.assert_called_once_with(candidate.context)


@pytest.mark.parametrize("answer", ["a handoff in flight", "no answer"])
def test_a_listener_that_is_not_at_rest_after_the_wait_keeps_the_journal(
    tmp_path: Path, *, answer: str, mocker: MockerFixture
) -> None:
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, preserved=True)
    pending = observed_installation(ctx)
    in_flight = {
        **interrupted.at_rest,
        "handoff_state": "ready",
        "handoff_transaction_id": interrupted.transaction.expected["transaction_id"],
    }
    bind_terminal = mocker.Mock()

    with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
        recover_transaction(
            ctx,
            runtime=interrupted.at_rest,
            read_after_wait=lambda: in_flight if answer == "a handoff in flight" else None,
            bind_terminal=bind_terminal,
        )

    assert observed_installation(ctx) == pending
    bind_terminal.assert_not_called()


@pytest.mark.parametrize(
    ("reached", "reverse", "preserved", "state"),
    [
        ("prepared", False, False, "closed"),
        ("prepared", True, False, "closed"),
        ("materialized", False, False, "rolled_back"),
        ("materialized", False, True, "rolled_back"),
        ("materialized", True, False, "rolled_back"),
        ("materialized", True, True, "rolled_back"),
    ],
)
def test_a_transaction_that_did_not_reach_activation_is_decided_without_a_wait(
    tmp_path: Path,
    *,
    reached: str,
    reverse: bool,
    preserved: bool,
    state: str,
    mocker: MockerFixture,
) -> None:
    """It requested no handoff: a predecessor at rest restores at once, as before."""
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(
        ctx, mocker=mocker, reached=reached, reverse=reverse, preserved=preserved
    )

    result = recover_transaction(ctx, runtime=interrupted.at_rest)

    assert result["state"] == state
    assert payload_generation.read(ctx) == interrupted.prior
    assert Path(ctx.command).samefile(interrupted.prior_context.executable)
    assert records.status(ctx) is None


def test_a_fresh_transaction_is_decided_without_a_wait(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """A fresh installation hands nothing off, activated or not."""
    ctx = control_root(tmp_path)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    candidate.commit_projection()
    candidate.activate()
    journal = records.journal_path(ctx).read_bytes()

    with pytest.raises(errors.InstallError, match="does not match the candidate projection"):
        recover_transaction(ctx, runtime={"pid": 321, "accepting": True, "draining": False})

    assert records.journal_path(ctx).read_bytes() == journal
    assert recover_transaction(ctx, runtime=None)["state"] == "rolled_back"
    assert records.status(ctx) is None


@pytest.mark.parametrize("shows", ["the candidate", "a handoff in flight", "no answer"])
def test_an_activated_transaction_waits_only_where_it_would_restore(
    tmp_path: Path, *, shows: str, mocker: MockerFixture
) -> None:
    """Every other decision on the first read stands as it is, at once."""
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, preserved=True)
    pending = observed_installation(ctx)

    if shows == "the candidate":
        assert recover_transaction(ctx, runtime=interrupted.committed)["state"] == "finalized"
        assert records.status(ctx) is None
        return
    runtime = (
        {**interrupted.at_rest, "handoff_state": "preparing"}
        if shows == "a handoff in flight"
        else None
    )
    with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
        recover_transaction(ctx, runtime=runtime)
    assert observed_installation(ctx) == pending


def test_an_installation_that_was_recorded_is_closed_without_a_wait(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    """The installed record names the transaction: its successor committed, whatever health says."""
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker)
    assert interrupted.committed is not None
    interruption = mocker.patch.object(
        completion, "close_finalized", side_effect=errors.InstallError("closing interrupted")
    )
    with pytest.raises(errors.InstallError, match="closing interrupted"):
        interrupted.transaction.finalize(interrupted.committed)
    mocker.stop(interruption)

    result = recover_transaction(ctx, runtime=interrupted.at_rest)

    assert result["state"] == "finalized"
    assert payload_generation.read(ctx) == payload_generation.Selection(
        str(interrupted.transaction.expected["transaction_id"]), interrupted.prior.active
    )
    assert records.status(ctx) is None
