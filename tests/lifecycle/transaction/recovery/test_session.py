"""What recovery reads in a listener's handoff session: at rest, committed, or neither.

A health read can be answered by a process that has handed its listener off: it accepted the
read's connection before its accept loop stopped. Such a predecessor shows the prior payload
with ``serving`` while the handoff is in flight, and with ``finalized`` and the transaction
that it handed off while it serves out its lease. Neither is at rest. A listener that an
earlier handoff created shows ``finalized`` with that handoff's transaction until its next one
begins, and that is at rest.

A successor shows ``serving`` until its predecessor has finalized the handoff, and the
predecessor may abort it until then. Recovery finalizes only a candidate that shows ``idle``,
as a listener that the supervisor started does, or ``finalized``.

Each case is decided alike on the first read and on the read that follows the wait.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import records
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.transaction.fixtures import InterruptedTransition
from tests.lifecycle.transaction.fixtures import interrupted_transition
from tests.lifecycle.transaction.fixtures import observed_installation
from tests.lifecycle.transaction.fixtures import recover_transaction

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context

_NOT_AT_REST = "payload recovery runtime does not match the prior selected generation"
_ANOTHER = "7" * 32
_READS = pytest.mark.parametrize("read", ["first", "after the wait"])
_DIRECTIONS = pytest.mark.parametrize("reverse", [False, True], ids=["upgrade", "rollback"])


def _journal_transaction(interrupted: InterruptedTransition) -> str:
    return str(interrupted.transaction.expected["transaction_id"])


def _session(
    runtime: dict[str, object] | None, state: str, transaction: str | None
) -> dict[str, object]:
    assert runtime is not None
    return {**runtime, "handoff_state": state, "handoff_transaction_id": transaction}


def _recover(
    ctx: runtime_context.RuntimeContext,
    interrupted: InterruptedTransition,
    shown: dict[str, object],
    read: str,
    *,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Recover with ``shown`` as the first read, or as the read that follows a wait at rest."""
    if read == "first":
        return recover_transaction(ctx, runtime=shown, bind_terminal=bind_terminal)
    return recover_transaction(
        ctx,
        runtime=interrupted.at_rest,
        read_after_wait=lambda: shown,
        bind_terminal=bind_terminal,
    )


@_READS
@_DIRECTIONS
@pytest.mark.parametrize(
    ("state", "names"),
    [
        ("finalized", "the journal"),
        ("serving", "the journal"),
        ("serving", "another"),
        ("serving", "nothing"),
        ("finalizing", "the journal"),
        ("committing", "the journal"),
    ],
)
def test_a_predecessor_that_hands_off_or_has_handed_off_is_not_at_rest(
    tmp_path: Path, *, read: str, reverse: bool, state: str, names: str, mocker: MockerFixture
) -> None:
    """The prior payload answers, in a handoff that is in flight or done: nothing is restored."""
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reverse=reverse, preserved=True)
    pending = observed_installation(ctx)
    transaction = {
        "the journal": _journal_transaction(interrupted),
        "another": _ANOTHER,
        "nothing": None,
    }[names]
    shown = _session(interrupted.at_rest, state, transaction)
    bind_terminal = mocker.Mock()

    with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
        _recover(ctx, interrupted, shown, read, bind_terminal=bind_terminal)

    assert observed_installation(ctx) == pending
    bind_terminal.assert_not_called()


@_READS
@_DIRECTIONS
@pytest.mark.parametrize("created_by", ["a handoff", "the supervisor"])
def test_a_predecessor_that_shows_how_it_was_created_is_at_rest_and_is_restored(
    tmp_path: Path, *, read: str, reverse: bool, created_by: str, mocker: MockerFixture
) -> None:
    """``finalized`` with the transaction of the handoff that created the listener, or ``idle``.

    A rollback's journal names the retained generation, and the listener that it would replace
    was created by the handoff of the generation that is selected: another transaction.
    """
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reverse=reverse, preserved=True)
    creating = interrupted.prior.active
    assert creating != _journal_transaction(interrupted)
    shown = (
        _session(interrupted.at_rest, "finalized", creating)
        if created_by == "a handoff"
        else _session(interrupted.at_rest, "idle", None)
    )
    waits: list[object] = []

    def read_after_wait() -> dict[str, object]:
        waits.append(observed_installation(ctx))
        return shown

    pending = observed_installation(ctx)
    result = recover_transaction(
        ctx,
        runtime=shown if read == "first" else interrupted.at_rest,
        read_after_wait=read_after_wait,
    )

    # At rest on the first read restores nothing at once: the request may be pending.
    assert waits == [pending]
    assert result["state"] == "rolled_back"
    assert payload_generation.read(ctx) == interrupted.prior
    assert Path(ctx.command).samefile(interrupted.prior_context.executable)
    assert records.status(ctx) is None


@_READS
@_DIRECTIONS
def test_a_candidate_that_shows_serving_is_not_finalized(
    tmp_path: Path, *, read: str, reverse: bool, mocker: MockerFixture
) -> None:
    """Its predecessor may still abort it: the journal stays for a later ``recover``."""
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reverse=reverse, preserved=True)
    pending = observed_installation(ctx)
    shown = _session(interrupted.committed, "serving", _journal_transaction(interrupted))
    bind_terminal = mocker.Mock()

    with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
        _recover(ctx, interrupted, shown, read, bind_terminal=bind_terminal)

    assert observed_installation(ctx) == pending
    bind_terminal.assert_not_called()


@_READS
@_DIRECTIONS
@pytest.mark.parametrize("created_by", ["a handoff", "the supervisor"])
def test_a_candidate_that_shows_finalized_or_idle_has_committed_and_is_finalized(
    tmp_path: Path, *, read: str, reverse: bool, created_by: str, mocker: MockerFixture
) -> None:
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reverse=reverse, preserved=True)
    transaction = _journal_transaction(interrupted)
    shown = (
        _session(interrupted.committed, "finalized", transaction)
        if created_by == "a handoff"
        else _session(interrupted.committed, "idle", None)
    )
    bind_terminal = mocker.Mock()

    result = _recover(ctx, interrupted, shown, read, bind_terminal=bind_terminal)

    assert result["state"] == "finalized"
    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.active == transaction
    installed = records.read_installed(ctx)
    assert installed is not None
    assert (installed["transaction_id"], installed["runtime"]) == (transaction, shown)
    assert records.status(ctx) is None
    bind_terminal.assert_called_once()


@pytest.mark.parametrize(("state", "names"), [("serving", "another"), ("finalized", "the journal")])
def test_a_materialized_rollback_is_restored_only_under_a_predecessor_at_rest(
    tmp_path: Path, *, state: str, names: str, mocker: MockerFixture
) -> None:
    """It requested no handoff and waits for none, and it restores under no handoff either.

    A rollback's request names the retained generation, so the session can hold the journal's
    transaction from an earlier rollback's request.
    """
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reached="materialized", reverse=True)
    pending = observed_installation(ctx)
    transaction = _journal_transaction(interrupted) if names == "the journal" else _ANOTHER

    with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
        recover_transaction(ctx, runtime=_session(interrupted.at_rest, state, transaction))
    assert observed_installation(ctx) == pending

    created = _session(interrupted.at_rest, "finalized", interrupted.prior.active)
    assert recover_transaction(ctx, runtime=created)["state"] == "rolled_back"
    assert payload_generation.read(ctx) == interrupted.prior
    assert records.status(ctx) is None
