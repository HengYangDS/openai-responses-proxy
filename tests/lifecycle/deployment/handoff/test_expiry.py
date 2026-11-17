"""An unanswered handoff request is settled once its timeout has passed with the listener at rest.

A listener begins a request only within the request's timeout of the accept of its
connection, and it accepted that connection before the connection of any later health read.
So once a health read has shown the captured listener at rest, the request can begin no later
than its timeout after that read, and a listener that is at rest after it will begin nothing.

The controller's clock moves an eighth of a second at each wait between two polls and stands
still otherwise, so each test names the reading at which the wait is over.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import PolledClock
from tests.lifecycle.deployment.handoff.fixtures import control_transport
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context

_TRANSACTION = str(expected_metadata()["transaction_id"])
_RESTING = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
_BEGUN = idle_runtime(handoff_state="preparing", handoff_transaction_id=_TRANSACTION)
_ANOTHER_HANDOFF = idle_runtime(handoff_state="committing", handoff_transaction_id="8" * 32)
_ANOTHER_PROCESS = idle_runtime(pid=1000)
_NONE = handoff.RequestEvidence.NONE
_OBSERVED = handoff.RequestEvidence.OBSERVED
_EXPIRED = handoff.RequestEvidence.EXPIRED
_POLL = PolledClock.POLL_SECONDS
# A request that carries one second is settled two seconds after the first read at rest.
_WAIT = 2.0

type Shows = Callable[[float], dict[str, object] | None]


def _unanswered() -> BaseException:
    return errors.InstallError("handoff control is unavailable")


class ExpiryFixture:
    """One captured listener, the real resolver and proof, and a clock that the polls move."""

    @pytest.fixture(autouse=True)
    def _capture_listener(self, tmp_path: Path, mocker: MockerFixture) -> None:
        self.ctx = install_context(tmp_path)
        self.source = process.OwnedProcess(999, self.ctx.executable, 0.5)
        self.mocker = mocker
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        self.clock = PolledClock(mocker)
        self.read_at: list[float] = []

    def health(self, shows: Shows) -> handoff.RuntimeReader:
        """Answer each health read with what the listener shows at that reading of the clock."""

        def read(_ctx: runtime_context.InstallationContext) -> dict[str, object] | None:
            self.read_at.append(self.clock.now)
            return shows(self.clock.now)

        return read

    def resolve(
        self,
        failure: BaseException,
        shows: Shows,
        *,
        captured: dict[str, object] | None = None,
        timeout: float = 1.0,
        lease: float = 1.0,
    ) -> tuple[str, dict[str, object] | None]:
        """Resolve from the clock's present reading; the deadline is timeout + lease + 5 later."""
        return handoff.resolve_after_controller_failure(
            self.ctx,
            captured if captured is not None else idle_runtime(),
            expected_metadata(),
            failure=failure,
            source_listener=self.source,
            runtime_reader=self.health(shows),
            timeout_seconds=timeout,
            lease_seconds=lease,
        )

    def proves(
        self,
        failure: BaseException,
        runtime: dict[str, object] | None,
        *,
        captured: dict[str, object] | None = None,
    ) -> bool:
        return handoff.predecessor_serves(
            self.ctx,
            failure=failure,
            source_listener=self.source,
            source_runtime=captured if captured is not None else idle_runtime(),
            runtime_reader=lambda _ctx: runtime,
        )


class TestTheWaitOfAnUnansweredRequest(ExpiryFixture):
    @pytest.mark.parametrize(
        ("captured", "shown"),
        [(idle_runtime(), idle_runtime()), (_RESTING, dict(_RESTING)), (_RESTING, idle_runtime())],
        ids=["idle", "unchanged", "idle-after-a-finished-session"],
    )
    @pytest.mark.parametrize(
        "failure",
        [_unanswered, KeyboardInterrupt, lambda: OSError("connection reset")],
        ids=["no-answer", "interrupt", "reset"],
    )
    def test_a_listener_that_stays_at_rest_settles_the_request_after_its_timeout_and_a_second(
        self,
        failure: Callable[[], BaseException],
        captured: dict[str, object],
        shown: dict[str, object],
    ) -> None:
        """Row "expired": at rest at the first read, and at rest when the wait is over."""
        ended = failure()

        assert self.resolve(ended, lambda _now: shown, captured=captured) == ("rolled_back", shown)

        assert self.read_at == [poll * _POLL for poll in range(17)]
        assert self.read_at[-1] == _WAIT
        assert self.clock.sleep.call_count == 16
        assert handoff.request_evidence(ended) is _EXPIRED

    def test_the_request_is_never_settled_before_the_wait_is_over(self) -> None:
        """Until then a listener at rest proves nothing, to the resolver and to the proof."""
        failure = _unanswered()
        before: list[tuple[handoff.RequestEvidence, bool]] = []

        def shows(_now: float) -> dict[str, object]:
            before.append((handoff.request_evidence(failure), self.proves(failure, idle_runtime())))
            return idle_runtime()

        assert self.resolve(failure, shows)[0] == "rolled_back"

        # Each read began without evidence, the one at which the wait was over among them.
        assert before == [(_NONE, False)] * 17
        assert self.read_at[-2:] == [_WAIT - _POLL, _WAIT]
        assert self.proves(failure, idle_runtime())

    def test_the_wait_is_counted_from_the_first_read_at_rest_and_not_from_the_failure(
        self,
    ) -> None:
        """A second without any answer comes first; the wait starts when the listener shows."""
        failure = _unanswered()

        def shows(now: float) -> dict[str, object] | None:
            return None if now < 1.0 else idle_runtime()

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        assert self.read_at[-1] == 1.0 + _WAIT
        assert handoff.request_evidence(failure) is _EXPIRED

    def test_the_wait_is_counted_from_the_moment_at_which_the_first_read_has_returned(
        self,
    ) -> None:
        """The listener answered that read no later than it returned, and maybe as late.

        The first read takes a second. A wait from the moment at which it was asked would be
        over at 2 seconds, before the request's time has run out for certain.
        """
        failure = _unanswered()

        def shows(_now: float) -> dict[str, object]:
            if len(self.read_at) == 1:
                self.clock.now += 1.0
            return idle_runtime()

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        assert self.read_at[:2] == [0.0, 1.0 + _POLL]
        assert self.read_at[-1] == 1.0 + _WAIT
        assert handoff.request_evidence(failure) is _EXPIRED

    def test_a_read_that_was_asked_before_the_wait_was_over_does_not_complete_it(self) -> None:
        """However late it returns: the listener may have answered it as soon as it was asked.

        The read that is asked an eighth of a second before the wait is over takes five
        eighths. Only the next one, asked after the wait, shows what the request could begin.
        """
        failure = _unanswered()

        def shows(now: float) -> dict[str, object]:
            if now == _WAIT - _POLL:
                self.clock.now += 5 * _POLL
            return idle_runtime()

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        assert self.read_at[-2:] == [_WAIT - _POLL, _WAIT + 5 * _POLL]
        assert handoff.request_evidence(failure) is _EXPIRED

    def test_other_reads_neither_start_the_wait_nor_start_it_again(self) -> None:
        """A read that fails, or shows another process or another handoff, changes nothing.

        The bound holds from the first answer of the captured listener at rest, whatever is
        read afterwards, and only a read that shows it at rest again completes the wait.
        """
        failure = _unanswered()
        shown = (
            (0.5, None),
            (1.0, _ANOTHER_PROCESS),
            # The one read at rest that starts the wait, at 1.0.
            (1.0 + _POLL, idle_runtime()),
            (1.5, None),
            (2.0, _ANOTHER_PROCESS),
            (2.5, _ANOTHER_HANDOFF),
        )

        def shows(now: float) -> dict[str, object] | None:
            return next((snapshot for until, snapshot in shown if now < until), idle_runtime())

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        # A wait from the first read of all would be over at 2.5; one started again there, at 4.5.
        assert self.read_at[-1] == 1.0 + _WAIT
        assert handoff.request_evidence(failure) is _EXPIRED

    def test_the_deadline_ends_a_wait_that_is_not_over_as_unknown(self) -> None:
        """The listener is at rest only from 5.5 seconds on, and the deadline is at 7."""
        failure = _unanswered()

        def shows(now: float) -> dict[str, object]:
            return _ANOTHER_HANDOFF if now < 5.5 else idle_runtime()

        assert self.resolve(failure, shows) == ("unknown", None)

        assert self.read_at[-1] == 7.0 - _POLL
        assert handoff.request_evidence(failure) is _NONE
        assert not self.proves(failure, idle_runtime())

    @pytest.mark.parametrize(
        "later",
        [_ANOTHER_HANDOFF, _ANOTHER_PROCESS, None],
        ids=["in-flight", "another-process", "no-answer"],
    )
    def test_a_listener_that_is_not_at_rest_when_the_wait_is_over_settles_nothing(
        self, later: dict[str, object] | None
    ) -> None:
        """Only a read that shows the captured listener at rest completes the wait."""
        failure = _unanswered()

        def shows(now: float) -> dict[str, object] | None:
            return idle_runtime() if now == 0.0 else later

        assert self.resolve(failure, shows) == ("unknown", None)

        assert self.read_at[-1] == 7.0 - _POLL
        assert handoff.request_evidence(failure) is _NONE

    @pytest.mark.parametrize(
        "failure",
        [_unanswered, lambda: handoff.HandoffRefusedError("handoff control returned HTTP 409")],
        ids=["no-answer", "refused"],
    )
    def test_a_listener_whose_captured_process_is_gone_settles_nothing(
        self, failure: Callable[[], BaseException]
    ) -> None:
        """Health that still shows the captured identity at rest is no proof without its process.

        The wait and every other evidence count only for the process that the controller
        captured: a process of that number that is no longer the captured one is another.
        """
        self.mocker.patch.object(process, "owned_process_alive", return_value=False)
        ended = failure()

        assert self.resolve(ended, lambda _now: idle_runtime()) == ("unknown", None)

        assert self.read_at[-1] == 7.0 - _POLL
        assert handoff.request_evidence(ended) is not _EXPIRED
        assert not self.proves(ended, idle_runtime())

    @pytest.mark.parametrize(
        ("caller", "carried"),
        [(0.5, 1), (1, 1), (30, 30), (120.9, 120), (500, 120)],
    )
    def test_the_wait_is_the_timeout_that_the_request_carried(
        self, caller: float, carried: int
    ) -> None:
        """The request and the wait take the timeout from one owner: whole seconds, 1 to 120."""
        sent = control_transport(self.mocker, side_effect=ConnectionResetError("reset"))
        with pytest.raises(errors.InstallError, match="handoff control is unavailable") as ended:
            handoff.request(
                self.ctx,
                expected_metadata(),
                source_listener=self.source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
                timeout_seconds=caller,
                lease_seconds=900,
            )

        assert json.loads(sent.call_args.args[1].data)["timeout_seconds"] == carried
        assert handoff.carried_timeout_seconds(caller) == carried
        assert self.resolve(ended.value, lambda _now: idle_runtime(), timeout=caller, lease=900)[
            0
        ] == ("rolled_back")
        assert self.read_at[0] == 0.0
        assert self.read_at[-1] == carried + 1.0


class TestAHandoffThatBeginsInsideTheWait(ExpiryFixture):
    def test_a_handoff_that_begins_and_rolls_back_is_resolved_when_it_ends(self) -> None:
        """It is observed and followed: its end resolves the request, before the wait is over."""
        failure = _unanswered()

        def shows(now: float) -> dict[str, object]:
            return _BEGUN if 1.0 <= now < 1.5 else idle_runtime()

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        assert self.read_at[-1] == 1.5
        assert handoff.request_evidence(failure) is _OBSERVED

    def test_a_handoff_that_is_in_flight_when_the_wait_is_over_is_followed_to_its_end(
        self,
    ) -> None:
        failure = _unanswered()

        def shows(now: float) -> dict[str, object]:
            return _BEGUN if 1.0 <= now < 3.0 else idle_runtime()

        assert self.resolve(failure, shows) == ("rolled_back", idle_runtime())

        assert self.read_at[-1] == 3.0
        assert handoff.request_evidence(failure) is _OBSERVED

    def test_a_handoff_that_commits_its_successor_resolves_as_finalized(self) -> None:
        failure = _unanswered()
        finalized = matching_health(1000, expected_metadata(), pid=1000, handoff_state="finalized")
        successor = process.OwnedProcess(1000, self.ctx.executable, 1.0)
        self.mocker.patch.object(process, "capture_executable", return_value=successor)
        self.mocker.patch.object(
            process,
            "owned_process_alive",
            side_effect=lambda owned: (owned.pid == successor.pid) == (self.clock.now >= 3.0),
        )

        def shows(now: float) -> dict[str, object]:
            if now < 1.0:
                return idle_runtime()
            return _BEGUN if now < 3.0 else finalized

        assert self.resolve(failure, shows) == ("finalized", finalized)

        assert self.read_at[-1] == 3.0
        assert handoff.request_evidence(failure) is _OBSERVED


class TestTheExpiredRowInTheProof(ExpiryFixture):
    def test_an_expired_request_proves_a_predecessor_that_is_idle_or_unchanged(self) -> None:
        """The proof reads what the resolver recorded on the failure."""
        failure = _unanswered()
        assert self.resolve(failure, lambda _now: idle_runtime())[0] == "rolled_back"
        assert handoff.request_evidence(failure) is _EXPIRED

        assert self.proves(failure, idle_runtime())
        assert self.proves(failure, dict(_RESTING), captured=_RESTING)
        assert self.proves(failure, idle_runtime(), captured=_RESTING)
        for runtime in (_BEGUN, _ANOTHER_HANDOFF, _ANOTHER_PROCESS, None):
            assert not self.proves(failure, runtime)
            assert not self.proves(failure, runtime, captured=_RESTING)
        # A finished session that the capture did not show is another handoff's.
        assert not self.proves(failure, dict(_RESTING))
        # Another failure, an interrupt of the resolver among them, carries none of it.
        assert not self.proves(_unanswered(), idle_runtime())
        assert not self.proves(KeyboardInterrupt(), idle_runtime())

    def test_the_rule_reads_an_expired_request_as_at_rest_when_idle_or_unchanged(self) -> None:
        resumed = handoff._old_runtime_resumed
        assert resumed(idle_runtime(), idle_runtime(), evidence=_EXPIRED)
        assert resumed(dict(_RESTING), _RESTING, evidence=_EXPIRED)
        assert resumed(idle_runtime(), _RESTING, evidence=_EXPIRED)
        assert not resumed(dict(_RESTING), idle_runtime(), evidence=_EXPIRED)
        assert not resumed(_BEGUN, idle_runtime(), evidence=_EXPIRED)
        assert not resumed(idle_runtime(accepting=False), idle_runtime(), evidence=_EXPIRED)
