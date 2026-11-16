"""A handoff request that the listener has read and not begun, in a forced order.

Each test drives the listener's own handler and session in one thread and reads its health
where the controller would: no thread, socket, process or real clock decides the order.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import TYPE_CHECKING
from typing import override

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import control as listener_control
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import idle_runtime

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context

_BINDINGS = listener_control.Bindings(
    runtime_status=entrypoint_module.runtime_status,
    handoff_context=entrypoint_module._handoff_context,
)


def _health(_ctx: runtime_context.RuntimeContext | None = None) -> dict[str, object]:
    """Read the listener's own session as the controller's health read shows it."""
    identity = handoff_module.runtime_identity(_BINDINGS.handoff_context())
    return idle_runtime(
        handoff_state=identity["handoff_state"],
        handoff_transaction_id=identity["handoff_transaction_id"],
    )


def _begin() -> None:
    """Take the session as the listener's ``prepare`` does in its first step."""
    with handoff_module._HANDOFF_LOCK:
        if handoff_module._HANDOFF_SESSION.get("state") == "finalized":
            handoff_module.reset_session_to_idle()
        handoff_module._transition("preparing")
        handoff_module._HANDOFF_SESSION.update(transaction_id=expected_metadata()["transaction_id"])


def _handle_request(at_prepare: Callable[[], None], *, mocker: MockerFixture) -> None:
    """Run the listener's handler on one handoff request up to where it calls ``prepare``."""

    def prepare(*_args: object, **_kwargs: object) -> None:
        at_prepare()
        raise handoff_module.HandoffError("the test stops the handoff here")

    mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
    mocker.patch.object(handoff_module, "prepare", side_effect=prepare)
    listener_control.prepare_handoff(fake_handler(expected_metadata(), mocker=mocker), _BINDINGS)


class TestAnUnansweredRequestInTheWindow(HandoffFixture):
    @override
    def setup_method(self) -> None:
        super().setup_method()
        handoff_module.reset_session_to_idle()
        self._cleanups.callback(handoff_module.reset_session_to_idle)

    def test_an_idle_listener_that_has_read_the_request_and_not_begun_resolves_nothing(
        self, *, mocker: MockerFixture
    ) -> None:
        """The window of task 12.5, in the order that loses the selection.

        The handler has compared the request with the disk and has not begun the handoff, so
        health shows the idle session that the controller captured. The controller's request
        ends without an answer in that moment. Its resolver and its proof must not read the
        idle session as a handoff that will not come: the handler goes on and begins it.
        """
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        captured = _health()
        seen: dict[str, object] = {}

        def controller_loses_its_answer() -> None:
            seen["read"] = _health()
            unanswered = errors.InstallError("handoff control is unavailable")
            # The deadline is 1 + 1 + 5 seconds after the first reading of the clock.
            clock = mocker.patch.object(
                handoff.time, "monotonic", side_effect=itertools.count(0.0, 3.0).__next__
            )
            sleep = mocker.patch.object(handoff.time, "sleep")
            seen["resolution"] = handoff.resolve_after_controller_failure(
                ctx,
                captured,
                expected_metadata(),
                failure=unanswered,
                source_listener=source,
                runtime_reader=_health,
                timeout_seconds=1,
                lease_seconds=1,
            )
            seen["waits"] = sleep.call_count
            mocker.stop(clock)
            mocker.stop(sleep)
            seen["proof"] = handoff.predecessor_serves(
                ctx,
                failure=unanswered,
                source_listener=source,
                source_runtime=captured,
                runtime_reader=_health,
            )
            # The handler goes on: it begins the handoff that the controller no longer expects.
            _begin()
            seen["begun"] = _health()

        _handle_request(controller_loses_its_answer, mocker=mocker)

        assert (captured["handoff_state"], captured["handoff_transaction_id"]) == ("idle", None)
        assert seen["read"] == captured
        assert seen["begun"] == idle_runtime(
            handoff_state="preparing",
            handoff_transaction_id=expected_metadata()["transaction_id"],
        )
        assert (seen["resolution"], seen["proof"]) == (("unknown", None), False)
        assert seen["waits"] == 2

    def test_a_listener_that_then_begins_and_fails_is_seen_and_resolved_before_the_deadline(
        self, *, mocker: MockerFixture
    ) -> None:
        """The same window, waited out: the resolver sees the request begin and end.

        Each health read is followed by the listener's next step. The session that held the
        request's transaction and is idle afterwards says that the handoff began and rolled
        back, and the proof reads the same evidence from the same failure.
        """
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        captured = _health()
        unanswered = errors.InstallError("handoff control is unavailable")
        seen: dict[str, object] = {}

        def controller_waits() -> None:
            steps = iter((_begin, lambda: handoff_module._reset_preparation(None)))
            sessions: list[object] = []

            def read_then_let_the_listener_go_on(
                _ctx: runtime_context.RuntimeContext,
            ) -> dict[str, object]:
                snapshot = _health()
                sessions.append(snapshot["handoff_state"])
                next(steps, lambda: None)()
                return snapshot

            clock = mocker.patch.object(
                handoff.time, "monotonic", side_effect=itertools.count(0.0, 0.01).__next__
            )
            sleep = mocker.patch.object(handoff.time, "sleep")
            seen["resolution"] = handoff.resolve_after_controller_failure(
                ctx,
                captured,
                expected_metadata(),
                failure=unanswered,
                source_listener=source,
                runtime_reader=read_then_let_the_listener_go_on,
                timeout_seconds=1,
                lease_seconds=1,
            )
            seen["sessions"] = sessions
            seen["waits"] = sleep.call_count
            mocker.stop(clock)
            mocker.stop(sleep)
            seen["proof"] = handoff.predecessor_serves(
                ctx,
                failure=unanswered,
                source_listener=source,
                source_runtime=captured,
                runtime_reader=_health,
            )

        _handle_request(controller_waits, mocker=mocker)

        assert seen["sessions"] == ["idle", "preparing", "idle"]
        assert (seen["resolution"], seen["proof"]) == (("rolled_back", captured), True)
        assert seen["waits"] == 2
        assert handoff.request_evidence(unanswered) is handoff.RequestEvidence.OBSERVED
