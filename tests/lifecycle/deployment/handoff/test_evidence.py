"""What a failed handoff request proves about the listener: the evidence and its rule.

The rule's table, one row for each kind of evidence, in the resolver and in the proof:

- unsent: the request failed before its first byte was written, its connect included.
- refused: the listener answered with an error status.
- acknowledged: the listener answered READY and the controller failed afterwards.
- none: the request ended without the listener's answer.
- observed: no answer, and the resolver has since seen the request's transaction in the session.
- expired: no answer, and the request's timeout has passed at rest; ``test_expiry.py`` holds it.
"""

from __future__ import annotations

import http.client
import io
import itertools
import urllib.error
from email.message import Message
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import control_transport
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health
from tests.serving.handoff.fixtures import ready_ack

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from pathlib import Path

    from pytest_mock import MockerFixture
    from pytest_mock import MockType

    from openai_responses_proxy.lifecycle import context as runtime_context

_TRANSACTION = str(expected_metadata()["transaction_id"])
_RESTING = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
_NONE = handoff.RequestEvidence.NONE
_UNSENT = handoff.RequestEvidence.UNSENT
_REFUSED = handoff.RequestEvidence.REFUSED
_ACKNOWLEDGED = handoff.RequestEvidence.ACKNOWLEDGED
_OBSERVED = handoff.RequestEvidence.OBSERVED


def _in_flight(state: str, **changes: object) -> dict[str, object]:
    """Return the captured listener's health while it holds this request's handoff."""
    return idle_runtime(handoff_state=state, handoff_transaction_id=_TRANSACTION, **changes)


def _refused() -> BaseException:
    return handoff.HandoffRefusedError("handoff control returned HTTP 409")


def _unanswered() -> BaseException:
    return errors.InstallError("handoff control is unavailable")


def _script(*snapshots: dict[str, object] | None) -> Callable[[object], dict[str, object] | None]:
    """Answer each health read with the next snapshot and then repeat the last one."""
    remaining = list(snapshots)

    def read(_ctx: object) -> dict[str, object] | None:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return read


class EvidenceFixture:
    """One captured listener, a patched clock and the real resolver, proof and request."""

    @pytest.fixture(autouse=True)
    def _capture_listener(self, tmp_path: Path, mocker: MockerFixture) -> None:
        self.ctx = install_context(tmp_path)
        self.source = process.OwnedProcess(999, self.ctx.executable, 0.5)
        self.mocker = mocker
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        self.control_request = control_transport(mocker)

    def clock(self, step: float) -> MockType:
        """Advance the controller's clock by ``step`` seconds at each reading; return its sleep."""
        self.mocker.patch.object(
            handoff.time, "monotonic", side_effect=itertools.count(0.0, step).__next__
        )
        return self.mocker.patch.object(handoff.time, "sleep")

    def no_wait(self) -> MockType:
        return self.mocker.patch.object(
            handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )

    def resolve(
        self,
        failure: BaseException,
        reader: Callable[[runtime_context.RuntimeContext], dict[str, object] | None],
        *,
        captured: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        """Resolve with a deadline of 1 + 1 + 5 seconds after the first reading of the clock."""
        return handoff.resolve_after_controller_failure(
            self.ctx,
            captured if captured is not None else idle_runtime(),
            expected_metadata(),
            failure=failure,
            source_listener=self.source,
            runtime_reader=reader,
            timeout_seconds=1,
            lease_seconds=1,
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

    def request(
        self,
        reader: Callable[[runtime_context.RuntimeContext], dict[str, object] | None],
        *,
        expected: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return handoff.request(
            self.ctx,
            expected if expected is not None else expected_metadata(),
            source_listener=self.source,
            source_runtime=idle_runtime(),
            runtime_reader=reader,
            timeout_seconds=1,
            lease_seconds=1,
        )

    def acknowledged(self) -> BaseException:
        """Return the failure of a real request that the listener acknowledged with READY."""
        self.control_request.side_effect = None
        self.control_request.return_value = Response(ready_ack(expected_metadata()))
        self.mocker.patch.object(process, "wait_for_executable", return_value=None)
        with pytest.raises(errors.InstallError, match="unverified successor") as raised:
            self.request(lambda _ctx: idle_runtime())
        return raised.value

    def unsent(self) -> BaseException:
        """Return the failure of a real request that found no captured listener to send to."""
        with pytest.raises(errors.InstallError, match="is no longer active") as raised:
            self.request(lambda _ctx: None)
        self.control_request.assert_not_called()
        return raised.value


class TestTheRuleInTheResolver(EvidenceFixture):
    def test_an_unsent_request_resolves_at_once_with_the_listener_idle_or_unchanged(self) -> None:
        """Row "unsent": the listener never received the request, so nothing of it can come."""
        failure = self.unsent()
        assert handoff.request_evidence(failure) is _UNSENT
        sleep = self.no_wait()
        for captured, shown in (
            (idle_runtime(), idle_runtime()),
            (_RESTING, dict(_RESTING)),
            (_RESTING, idle_runtime()),
        ):
            assert self.resolve(failure, lambda _ctx, shown=shown: shown, captured=captured) == (
                "rolled_back",
                shown,
            )
        sleep.assert_not_called()

    def test_an_unsent_request_is_not_at_rest_while_a_handoff_is_in_flight(self) -> None:
        """Another handoff of the captured listener is waited out, and its end is at rest."""
        failure = self.unsent()
        other = idle_runtime(handoff_state="committing", handoff_transaction_id="8" * 32)
        sleep = self.clock(3.0)
        assert self.resolve(failure, lambda _ctx: other) == ("unknown", None)
        assert sleep.call_count == 2

        sleep = self.clock(0.01)
        assert self.resolve(failure, _script(other, other, idle_runtime())) == (
            "rolled_back",
            idle_runtime(),
        )
        assert sleep.call_count == 2
        assert handoff.request_evidence(failure) is _UNSENT

    def test_a_refused_request_resolves_at_once_with_the_listener_idle_or_unchanged(self) -> None:
        """Row "refused": nothing began, or it began and ended."""
        sleep = self.no_wait()
        for captured, shown in (
            (idle_runtime(), idle_runtime()),
            (_RESTING, dict(_RESTING)),
            (_RESTING, idle_runtime()),
        ):
            assert self.resolve(_refused(), lambda _ctx, shown=shown: shown, captured=captured) == (
                "rolled_back",
                shown,
            )
        sleep.assert_not_called()

    def test_an_acknowledged_request_that_the_listener_rolls_back_resolves_at_once(self) -> None:
        """Row "acknowledged": the handoff began, and an idle session says that it ended."""
        failure = self.acknowledged()
        assert handoff.request_evidence(failure) is _ACKNOWLEDGED
        sleep = self.no_wait()
        for captured in (idle_runtime(), _RESTING):
            assert self.resolve(failure, lambda _ctx: idle_runtime(), captured=captured) == (
                "rolled_back",
                idle_runtime(),
            )
        sleep.assert_not_called()

    def test_an_acknowledged_request_is_followed_while_its_handoff_is_in_flight(self) -> None:
        """The READY answer does not end the wait: only the session at rest does."""
        failure = self.acknowledged()
        sleep = self.clock(0.1)
        reader = _script(
            _in_flight("ready"), _in_flight("committing"), _in_flight("aborting"), idle_runtime()
        )
        assert self.resolve(failure, reader) == ("rolled_back", idle_runtime())
        assert sleep.call_count == 3

    def test_an_acknowledged_request_does_not_rest_on_the_captured_finished_session(self) -> None:
        """A handoff that began has replaced that session, so it is no evidence of an end."""
        failure = self.acknowledged()
        sleep = self.clock(3.0)
        assert self.resolve(failure, lambda _ctx: dict(_RESTING), captured=_RESTING) == (
            "unknown",
            None,
        )
        assert sleep.call_count == 2

    @pytest.mark.parametrize(
        "failure",
        [
            errors.InstallError("handoff control is unavailable"),
            errors.InstallError("captured proxy listener generation 999 is no longer active"),
            errors.InstallError("handoff control returned HTTP 200"),
            KeyboardInterrupt(),
            OSError("connection reset"),
        ],
        ids=["no-answer", "unrecorded", "not-ready", "interrupt", "reset"],
    )
    @pytest.mark.parametrize("captured", [idle_runtime(), _RESTING], ids=["idle", "resting"])
    def test_an_unanswered_request_against_a_listener_at_rest_is_unknown_before_its_timeout(
        self, failure: BaseException, captured: dict[str, object]
    ) -> None:
        """Row "none": the listener may have read the request and be about to begin it.

        One read at rest proves nothing. Here the deadline ends the resolver before the
        request's timeout has passed at rest.
        """
        sleep = self.clock(3.0)
        reader = self.mocker.Mock(return_value=dict(captured))

        assert self.resolve(failure, reader, captured=captured) == ("unknown", None)

        # Readings at 0 (the deadline, 7) and 3 poll once; the read has returned at 6, where
        # the wait of 2 seconds at rest starts; the reading at 9 ends the resolver.
        assert reader.call_count == 1
        assert sleep.call_count == 1
        assert handoff.request_evidence(failure) is _NONE

    def test_an_unanswered_request_that_begins_and_ends_resolves_before_the_deadline(
        self, subtests: pytest.Subtests
    ) -> None:
        """Row "observed": the session held this request's transaction and is idle afterwards."""
        journeys: tuple[tuple[str, tuple[dict[str, object] | None, ...]], ...] = (
            ("prepare fails", (idle_runtime(), _in_flight("preparing"), idle_runtime())),
            (
                "commit fails",
                (
                    idle_runtime(),
                    None,
                    _in_flight("ready"),
                    _in_flight("committing"),
                    # The successor answers health between COMMIT and its abort.
                    matching_health(1000, expected_metadata(), pid=1000),
                    _in_flight("aborting"),
                    _in_flight("rolled_back"),
                    idle_runtime(),
                ),
            ),
            ("a listener that a handoff created", (dict(_RESTING), _in_flight("preparing"))),
        )
        for name, snapshots in journeys:
            with subtests.test(journey=name):
                failure = _unanswered()
                captured = _RESTING if snapshots[0] == _RESTING else idle_runtime()
                sleep = self.clock(0.01)
                reader = self.mocker.Mock(side_effect=_script(*snapshots, idle_runtime()))

                assert self.resolve(failure, reader, captured=captured) == (
                    "rolled_back",
                    idle_runtime(),
                )

                # It returned at the first idle reading after the transaction, far from 7 s.
                assert reader.call_count == len(snapshots) + (snapshots[-1] != idle_runtime())
                assert sleep.call_count == reader.call_count - 1
                assert handoff.request_evidence(failure) is _OBSERVED

    def test_another_transaction_in_the_session_is_no_observation_of_this_request(self) -> None:
        """Some other handoff began and ended; this request may still be read and begun.

        The idle session after it resolves nothing at once, as an observation would. The
        request is settled by its own timeout: the wait of 2 seconds at rest starts at 3,
        when the first idle read has returned, and is over for the read that is asked at 5.
        """
        failure = _unanswered()
        sleep = self.clock(1.0)
        other = idle_runtime(handoff_state="preparing", handoff_transaction_id="8" * 32)
        reader = self.mocker.Mock(side_effect=_script(other, idle_runtime()))

        assert self.resolve(failure, reader) == ("rolled_back", idle_runtime())

        assert (reader.call_count, sleep.call_count) == (4, 3)
        assert handoff.request_evidence(failure) is handoff.RequestEvidence.EXPIRED

    def test_an_observed_request_is_followed_to_its_finalized_successor(self) -> None:
        """A handoff that begins after an unanswered request and commits resolves as finalized."""
        failure = _unanswered()
        expected = expected_metadata()
        finalized = matching_health(1000, expected, pid=1000, handoff_state="finalized")
        successor = process.OwnedProcess(1000, self.ctx.executable, 1.0)
        self.mocker.patch.object(process, "capture_executable", return_value=successor)
        gone: list[bool] = []
        self.mocker.patch.object(
            process,
            "owned_process_alive",
            side_effect=lambda owned: owned.pid == successor.pid or not gone,
        )
        self.clock(0.01)

        def read(_ctx: object) -> dict[str, object]:
            snapshot = next(snapshots)
            if snapshot is finalized:
                gone.append(True)
            return snapshot

        snapshots = iter((idle_runtime(), _in_flight("preparing"), _in_flight("ready"), finalized))
        assert self.resolve(failure, read) == ("finalized", finalized)


class TestTheRuleInTheProof(EvidenceFixture):
    def test_an_unsent_request_proves_a_predecessor_that_is_idle_or_unchanged(self) -> None:
        """Row "unsent"."""
        failure = self.unsent()
        assert self.proves(failure, idle_runtime())
        assert self.proves(failure, dict(_RESTING), captured=_RESTING)
        assert self.proves(failure, idle_runtime(), captured=_RESTING)
        for state in ("preparing", "ready", "committing", "aborting", "rolled_back"):
            assert not self.proves(failure, _in_flight(state))
        # A finished session that the capture did not show is another handoff's.
        assert not self.proves(failure, dict(_RESTING))

    def test_a_refused_request_proves_a_predecessor_that_is_idle_or_unchanged(self) -> None:
        """Row "refused"."""
        assert self.proves(_refused(), idle_runtime())
        assert self.proves(_refused(), dict(_RESTING), captured=_RESTING)
        assert self.proves(_refused(), idle_runtime(), captured=_RESTING)
        assert not self.proves(_refused(), _in_flight("preparing"))
        assert not self.proves(_refused(), dict(_RESTING))

    def test_an_acknowledged_request_proves_only_an_idle_predecessor(self) -> None:
        """Row "acknowledged"."""
        failure = self.acknowledged()
        assert self.proves(failure, idle_runtime())
        assert self.proves(failure, idle_runtime(), captured=_RESTING)
        assert not self.proves(failure, dict(_RESTING), captured=_RESTING)
        for state in ("ready", "committing", "aborting", "rolled_back"):
            assert not self.proves(failure, _in_flight(state))

    def test_an_unanswered_request_proves_nothing(self) -> None:
        """Row "none": neither an idle session nor the captured finished one."""
        for failure in (_unanswered(), KeyboardInterrupt(), OSError("connection reset")):
            assert not self.proves(failure, idle_runtime())
            assert not self.proves(failure, dict(_RESTING), captured=_RESTING)
            assert not self.proves(failure, idle_runtime(), captured=_RESTING)

    def test_an_observed_request_proves_an_idle_predecessor_with_the_same_failure(self) -> None:
        """Row "observed": the proof reads what the resolver recorded on the failure."""
        failure = _unanswered()
        self.clock(0.01)
        assert self.resolve(failure, _script(_in_flight("preparing"), idle_runtime()))[0] == (
            "rolled_back"
        )

        assert self.proves(failure, idle_runtime())
        assert self.proves(failure, idle_runtime(), captured=_RESTING)
        assert not self.proves(failure, dict(_RESTING), captured=_RESTING)
        assert not self.proves(failure, _in_flight("preparing"))
        # Another failure, an interrupt of the resolver among them, carries none of it.
        assert not self.proves(_unanswered(), idle_runtime())
        assert not self.proves(KeyboardInterrupt(), idle_runtime())
        self.control_request.assert_not_called()


class TestWhatTheRequestRecords(EvidenceFixture):
    def _ends(
        self,
        failure: type[BaseException],
        health: Iterable[dict[str, object] | BaseException | None],
        *,
        expected: dict[str, object] | None = None,
    ) -> BaseException:
        """Return what ends a real request whose health reads answer or raise in this order."""
        readings = iter(health)

        def read(_ctx: object) -> dict[str, object] | None:
            reading = next(readings)
            if isinstance(reading, BaseException):
                raise reading
            return reading

        with pytest.raises(failure) as raised:
            self.request(read, expected=expected)
        return raised.value

    def test_a_failure_before_the_connection_opens_carries_that_nothing_was_sent(
        self, subtests: pytest.Subtests
    ) -> None:
        """The captured-listener check, the building of the request, and an interrupt there."""
        interrupt = KeyboardInterrupt
        too_large = expected_metadata(release="x" * handoff._MAX_BODY_BYTES)
        incomplete = {"transaction_id": _TRANSACTION}
        points: tuple[
            tuple[
                str,
                type[BaseException],
                dict[str, object] | BaseException | None,
                dict[str, object] | None,
            ],
            ...,
        ] = (
            (
                "the captured listener is replaced",
                errors.InstallError,
                idle_runtime(pid=1000),
                None,
            ),
            ("the captured listener is silent", errors.InstallError, None, None),
            ("health is unreadable", OSError, OSError("unreadable"), None),
            ("an interrupt during the check", interrupt, interrupt(), None),
            ("the request is too large", errors.InstallError, idle_runtime(), too_large),
            ("the request is incomplete", KeyError, idle_runtime(), incomplete),
        )
        for name, raised, health, expected in points:
            with subtests.test(point=name):
                failure = self._ends(raised, [health], expected=expected)
                assert handoff.request_evidence(failure) is _UNSENT
        with subtests.test(point="an interrupt while the request is built"):
            self.mocker.patch.object(
                handoff.runtime_config, "loopback_url", side_effect=interrupt()
            )
            failure = self._ends(interrupt, [idle_runtime()])
            assert handoff.request_evidence(failure) is _UNSENT
        self.control_request.assert_not_called()

    def test_a_failure_of_the_sending_call_carries_no_evidence(
        self, subtests: pytest.Subtests
    ) -> None:
        """Once the request is written the listener may have it, whatever ends the call."""
        for transport in (
            ConnectionRefusedError("refused"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            urllib.error.URLError("offline"),
            http.client.RemoteDisconnected("closed"),
            http.client.IncompleteRead(b"{"),
        ):
            with subtests.test(point=type(transport).__name__):
                self.control_request.side_effect = transport
                failure = self._ends(errors.InstallError, [idle_runtime()])
                assert str(failure) == "handoff control is unavailable"
                assert handoff.request_evidence(failure) is _NONE
        with subtests.test(point="an interrupt during the send"):
            self.control_request.side_effect = KeyboardInterrupt()
            failure = self._ends(KeyboardInterrupt, [idle_runtime()])
            assert handoff.request_evidence(failure) is _NONE
        assert self.control_request.call_count == 7

    def test_an_answer_that_is_no_ready_acknowledgement_carries_no_evidence(
        self, subtests: pytest.Subtests
    ) -> None:
        """Only a complete READY for this transaction says that the listener began it."""
        ready = ready_ack(expected_metadata())
        for name, response in (
            ("another success status", Response(ready, status=200)),
            ("no JSON", Response(b"READY")),
            ("no object", Response(b"[]")),
            ("too large", Response(b"x" * (handoff._MAX_BODY_BYTES + 1))),
            ("not ready", Response({**ready, "state": "preparing"})),
            ("another protocol", Response({**ready, "protocol_version": 1})),
            ("another transaction", Response({**ready, "transaction_id": "8" * 32})),
            ("no child", Response({**ready, "child_pid": 0})),
        ):
            with subtests.test(answer=name):
                self.control_request.side_effect = None
                self.control_request.return_value = response
                failure = self._ends(errors.InstallError, [idle_runtime()])
                assert handoff.request_evidence(failure) is _NONE

    def test_an_error_status_is_the_refusal(self) -> None:
        for status in (400, 403, 408, 409, 413, 503):
            body = io.BytesIO(b"refused")
            self.control_request.side_effect = urllib.error.HTTPError(
                "url", status, "refused", Message(), body
            )
            failure = self._ends(handoff.HandoffRefusedError, [idle_runtime()])
            assert handoff.request_evidence(failure) is _REFUSED
            assert body.closed

    def test_every_failure_after_the_ready_answer_carries_the_acknowledgement(
        self, subtests: pytest.Subtests
    ) -> None:
        """The successor's verification, the convergence and an interrupt anywhere in them."""
        successor = process.OwnedProcess(1000, self.ctx.executable, 1.0)
        in_flight = _in_flight("committing")
        interrupt = KeyboardInterrupt
        # What ends the request; then the successor's verification, each later health read,
        # the wait between two readings, and the clock's step.
        points: tuple[
            tuple[
                str,
                type[BaseException],
                process.OwnedProcess | BaseException | None,
                dict[str, object] | BaseException,
                BaseException | None,
                float,
            ],
            ...,
        ] = (
            ("the successor is not verified", errors.InstallError, None, in_flight, None, 0.01),
            ("an interrupt while it is verified", interrupt, interrupt(), in_flight, None, 0.01),
            ("health is unreadable", OSError, successor, OSError("unreadable"), None, 0.01),
            (
                "an interrupt between two readings",
                interrupt,
                successor,
                in_flight,
                interrupt(),
                0.01,
            ),
            ("the convergence deadline", errors.InstallError, successor, in_flight, None, 100.0),
        )
        for name, raised, verified, health, wait, step in points:
            with subtests.test(point=name):
                self.control_request.side_effect = None
                self.control_request.return_value = Response(ready_ack(expected_metadata()))
                self.mocker.patch.object(
                    process,
                    "wait_for_executable",
                    side_effect=verified if isinstance(verified, BaseException) else None,
                    return_value=verified,
                )
                self.clock(step).side_effect = wait
                failure = self._ends(
                    raised, itertools.chain([idle_runtime()], itertools.repeat(health))
                )
                assert handoff.request_evidence(failure) is _ACKNOWLEDGED

    def test_the_evidence_is_read_from_the_failure_and_never_from_its_message(self) -> None:
        for message in (
            "captured proxy listener generation 999 is no longer active",
            "handoff request payload is too large",
            "handoff control returned HTTP 409",
            "handoff did not converge on finalized listener 1000 within 9s",
            "handoff control response names an unverified successor process",
        ):
            assert handoff.request_evidence(errors.InstallError(message)) is _NONE
            assert not self.proves(errors.InstallError(message), idle_runtime())
