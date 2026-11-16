"""A listener begins a handoff only within the request's timeout of the accept of its connection.

A patched clock forces each side of the bound in one thread: no thread, socket, process or
real clock decides an order.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING
from typing import Self

import pytest

from openai_responses_proxy.serving import control
from tests.serving.handoff.fixtures import HandoffFixture
from tests.serving.handoff.fixtures import child_message
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_child
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import fake_server
from tests.serving.handoff.fixtures import handoff_module

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

_ACCEPTED = 100.0
_FINISHED = "7" * 32
_SESSIONS = pytest.mark.parametrize("session", ["idle", "finalized"])
_TIMEOUTS = pytest.mark.parametrize("timeout", [0.1, 30.0, 120.0])


def _just_after(moment: float) -> float:
    """Return the first reading of the clock that is later than ``moment``."""
    return math.nextafter(moment, math.inf)


class BoundFixture(HandoffFixture):
    """The listener's session, a replacement child that answers READY, and a patched clock."""

    @pytest.fixture(autouse=True)
    def _replacement(self, mocker: MockerFixture) -> None:
        self.mocker = mocker
        self.child = fake_child(mocker=mocker)
        self.child.recv_message.return_value = child_message(
            "ready", self.child, expected_metadata()
        )
        self.spawn = mocker.patch.object(handoff_module, "spawn_child", return_value=self.child)

    def at(self, now: float) -> None:
        """Let the listener's clock read ``now``."""
        self.mocker.patch.object(handoff_module.time, "monotonic", return_value=now)

    def rest(self, session: str) -> dict[str, object]:
        """Put the session at rest, idle or showing a finished handoff; return what it holds."""
        if session == "finalized":
            handoff_module._HANDOFF_SESSION.update(state="finalized", transaction_id=_FINISHED)
        return dict(handoff_module._HANDOFF_SESSION)

    def prepare(self, *, accepted_at: float | None, timeout: float = 30.0) -> object:
        return handoff_module.prepare(
            fake_server(mocker=self.mocker),
            expected_metadata(),
            self.context,
            accepted_at=accepted_at,
            timeout_seconds=timeout,
        )


class TestTheBoundOfTheBegin(BoundFixture):
    @_SESSIONS
    @_TIMEOUTS
    def test_a_request_begins_up_to_its_timeout_after_the_accept(
        self, session: str, timeout: float
    ) -> None:
        """Just inside: the clock reads the accept's stamp plus the request's own timeout."""
        self.rest(session)
        self.at(_ACCEPTED + timeout)

        self.prepare(accepted_at=_ACCEPTED, timeout=timeout)

        held = handoff_module._HANDOFF_SESSION
        assert (held["state"], held["transaction_id"]) == ("ready", "txn-1")
        self.spawn.assert_called_once()

    @_SESSIONS
    @_TIMEOUTS
    def test_a_request_is_refused_once_its_timeout_has_passed_since_the_accept(
        self, session: str, timeout: float
    ) -> None:
        """Just outside: the next reading of the clock. The session is as it was."""
        before = self.rest(session)
        self.at(_just_after(_ACCEPTED + timeout))

        with pytest.raises(handoff_module.HandoffRequestExpiredError) as refused:
            self.prepare(accepted_at=_ACCEPTED, timeout=timeout)

        assert isinstance(refused.value, handoff_module.HandoffError)
        assert not isinstance(refused.value, handoff_module.HandoffConflictError)
        assert dict(handoff_module._HANDOFF_SESSION) == before
        assert before["state"] == session
        self.spawn.assert_not_called()

    @_SESSIONS
    def test_a_request_without_a_stamp_is_refused(self, session: str) -> None:
        """No accept is known for it, so no moment bounds its begin."""
        before = self.rest(session)
        self.at(0.0)

        with pytest.raises(handoff_module.HandoffRequestExpiredError):
            self.prepare(accepted_at=None)

        assert dict(handoff_module._HANDOFF_SESSION) == before
        self.spawn.assert_not_called()

    def test_a_late_request_leaves_a_handoff_in_flight_as_it_is(self) -> None:
        handoff_module._HANDOFF_SESSION.update(state="ready", transaction_id=_FINISHED)
        before = dict(handoff_module._HANDOFF_SESSION)
        self.at(_just_after(_ACCEPTED + 30.0))

        with pytest.raises(handoff_module.HandoffError):
            self.prepare(accepted_at=_ACCEPTED)

        assert dict(handoff_module._HANDOFF_SESSION) == before
        self.spawn.assert_not_called()


class _HeldElsewhere:
    """The session's lock, which its other holder releases only after ``wait`` seconds."""

    def __init__(self, wait: float, *, now: float) -> None:
        """Start the listener's clock at ``now`` with the lock held elsewhere."""
        self.wait = wait
        self.now = now
        self.depth = 0
        self.read_under_the_lock: list[bool] = []
        self.shown_at_release: list[tuple[object, object]] = []

    def __enter__(self) -> Self:
        """Take the lock, after the wait for it where this thread does not hold it yet."""
        if not self.depth:
            self.now += self.wait
        self.depth += 1
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release one level of the lock; record what the session shows once it is free."""
        self.depth -= 1
        if not self.depth:
            session = handoff_module._HANDOFF_SESSION
            self.shown_at_release.append((session.get("state"), session.get("transaction_id")))

    def monotonic(self) -> float:
        self.read_under_the_lock.append(self.depth > 0)
        return self.now


class TestTheBoundIsReadUnderTheLock(BoundFixture):
    def held_elsewhere(self, wait: float, *, now: float) -> _HeldElsewhere:
        lock = _HeldElsewhere(wait, now=now)
        self.mocker.patch.object(handoff_module, "_HANDOFF_LOCK", lock)
        self.mocker.patch.object(handoff_module.time, "monotonic", side_effect=lock.monotonic)
        return lock

    def test_a_request_that_waits_for_the_lock_past_its_bound_is_refused(self) -> None:
        """It arrives ten seconds inside its bound and gets the lock ten seconds outside it."""
        before = dict(handoff_module._HANDOFF_SESSION)
        lock = self.held_elsewhere(20.0, now=_ACCEPTED + 20.0)

        with pytest.raises(handoff_module.HandoffRequestExpiredError):
            self.prepare(accepted_at=_ACCEPTED, timeout=30.0)

        assert lock.read_under_the_lock == [True]
        assert dict(handoff_module._HANDOFF_SESSION) == before
        self.spawn.assert_not_called()

    def test_a_request_that_gets_the_lock_inside_its_bound_begins(self) -> None:
        lock = self.held_elsewhere(10.0, now=_ACCEPTED + 20.0)

        self.prepare(accepted_at=_ACCEPTED, timeout=30.0)

        assert lock.read_under_the_lock == [True]
        assert handoff_module._HANDOFF_SESSION["state"] == "ready"

    def test_a_begin_shows_its_session_before_it_releases_the_lock(self) -> None:
        """So no health read finds a session at rest after a begin has passed the bound."""
        lock = self.held_elsewhere(0.0, now=_ACCEPTED)

        self.prepare(accepted_at=_ACCEPTED)

        assert lock.shown_at_release[0] == ("preparing", "txn-1")

    def test_a_health_read_reads_the_session_under_the_lock_of_the_begin(self) -> None:
        lock = self.held_elsewhere(0.0, now=_ACCEPTED)
        held: list[int] = []

        def manifest() -> str:
            held.append(lock.depth)
            return "b" * 64

        object.__setattr__(self.context, "payload_manifest_sha256", manifest)

        identity = handoff_module.runtime_identity(self.context)

        assert held == [1]
        assert (identity["handoff_state"], identity["handoff_transaction_id"]) == ("idle", None)
        assert lock.shown_at_release == [("idle", None)]


class TestTheHandlerRefusesALateRequest(BoundFixture):
    def handle(
        self, body: dict[str, object], *, accepted_at: float | None, now: float
    ) -> tuple[int, dict[str, object], MockType]:
        """Run the listener's handler on one request; return its status, body and commit thread."""
        handoff_module.reset_session_to_idle()
        self.before = dict(handoff_module._HANDOFF_SESSION)
        self.at(now)
        self.mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        commit = self.mocker.patch("threading.Thread")
        handler = fake_handler({**expected_metadata(), **body}, mocker=self.mocker)
        written: list[bytes] = []
        handler.wfile.write.side_effect = written.append
        bindings = control.Bindings(
            runtime_status=entrypoint_module.runtime_status,
            handoff_context=lambda: self.context,
        )

        control.prepare_handoff(handler, bindings, accepted_at=accepted_at)

        handler.send_error.assert_not_called()
        handler.send_response.assert_called_once()
        status: object = handler.send_response.call_args.args[0]
        answer: object = json.loads(b"".join(written))
        assert isinstance(status, int)
        assert isinstance(answer, dict)
        return status, {str(key): value for key, value in answer.items()}, commit

    @pytest.mark.parametrize(
        ("body", "bound"),
        [
            ({}, 30.0),
            ({"timeout_seconds": 5}, 5.0),
            ({"timeout_seconds": 0}, 0.1),
            ({"timeout_seconds": "9999"}, 120.0),
        ],
        ids=["default", "five", "least", "most"],
    )
    def test_the_bound_is_the_timeout_that_the_handler_parsed_from_the_request(
        self, body: dict[str, object], bound: float
    ) -> None:
        status, answer, commit = self.handle(body, accepted_at=_ACCEPTED, now=_ACCEPTED + bound)
        assert (status, answer["state"]) == (202, "ready")
        commit.return_value.start.assert_called_once_with()

        self.spawn.reset_mock()
        status, answer, commit = self.handle(
            body, accepted_at=_ACCEPTED, now=_just_after(_ACCEPTED + bound)
        )
        assert status == 408
        assert answer == {"ok": False, "error": "handoff_request_expired"}
        commit.assert_not_called()
        assert dict(handoff_module._HANDOFF_SESSION) == self.before
        self.spawn.assert_not_called()

    def test_a_request_whose_connection_has_no_stamp_gets_the_same_refusal(
        self,
    ) -> None:
        status, answer, commit = self.handle({}, accepted_at=None, now=0.0)

        assert status == 408
        assert answer == {"ok": False, "error": "handoff_request_expired"}
        commit.assert_not_called()
        assert dict(handoff_module._HANDOFF_SESSION) == self.before
        self.spawn.assert_not_called()
