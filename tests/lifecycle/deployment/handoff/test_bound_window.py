"""The window of an unanswered handoff request, closed from both sides in a forced order.

The listener's own handler and session and the controller's own resolver and proof run in one
thread and read one clock, as two processes of one host read one monotonic clock. The clock
moves only while the controller waits between two polls: no thread, socket, process or real
clock decides an order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from typing import override

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import control as listener_control
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.deployment.handoff.fixtures import PolledClock
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import child_message
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_child
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import idle_runtime

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer

    from pytest_mock import MockerFixture
    from pytest_mock import MockType

    from openai_responses_proxy.lifecycle import context as runtime_context

# The request carries 30 seconds: the listener may begin it until 30 seconds after the accept,
# and the controller settles it 31 seconds after its first read of the listener at rest.
_TIMEOUT = 30
_REQUEST = {**expected_metadata(), "timeout_seconds": _TIMEOUT}


class TestTheWindowFromBothSides(HandoffFixture):
    @override
    def setup_method(self) -> None:
        super().setup_method()
        handoff_module.reset_session_to_idle()
        self._cleanups.callback(handoff_module.reset_session_to_idle)
        context = entrypoint_module._handoff_context()
        object.__setattr__(context, "successor_executable", lambda: context.executable)
        self.bindings = listener_control.Bindings(
            runtime_status=entrypoint_module.runtime_status,
            handoff_context=lambda: context,
        )
        self.ctx = install_context(Path(self.tempdir.name))
        self.source = process.OwnedProcess(999, self.ctx.executable, 0.5)

    def health(self, _ctx: runtime_context.RuntimeContext | None = None) -> dict[str, object]:
        """Read the listener's own session as the controller's health read shows it."""
        identity = handoff_module.runtime_identity(self.bindings.handoff_context())
        return idle_runtime(
            handoff_state=identity["handoff_state"],
            handoff_transaction_id=identity["handoff_transaction_id"],
        )

    def handler(self, mocker: MockerFixture) -> tuple[MockType, list[bytes]]:
        """Return the listener's handler of the request and what it writes as its answer."""
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        handler = fake_handler(_REQUEST, mocker=mocker)
        written: list[bytes] = []
        handler.wfile.write.side_effect = written.append
        return handler, written

    def test_a_handler_that_reaches_its_begin_after_the_controller_settled_is_refused(
        self, *, mocker: MockerFixture
    ) -> None:
        """The order that lost the selection at 6.0.1, in its tightest form.

        The listener accepts the request's connection and answers the controller's first
        health read at the same reading of the clock. Its handler has read the request and
        stands before the begin when the controller loses its answer. The controller waits
        the request's timeout and a second, sees the listener still at rest and restores. The
        handler goes on only then: its bound passed a second earlier, and it is refused.
        """
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        clock = PolledClock(mocker)
        accepted_at = clock.now
        captured = self.health()
        handler, written = self.handler(mocker)
        spawn = mocker.patch.object(handoff_module, "spawn_child")
        begin = handoff_module.prepare
        seen: dict[str, object] = {}

        def controller_settles_before_the_handler_goes_on(
            server: ThreadingHTTPServer,
            expected: dict[str, object],
            context: handoff_module.Context,
            *,
            accepted_at: float | None,
            timeout_seconds: float,
            lease_seconds: float,
        ) -> handoff_module.PreparedHandoff:
            unanswered = errors.InstallError("handoff control is unavailable")
            seen["resolution"] = handoff.resolve_after_controller_failure(
                self.ctx,
                captured,
                expected_metadata(),
                failure=unanswered,
                source_listener=self.source,
                runtime_reader=self.health,
                timeout_seconds=_TIMEOUT,
                lease_seconds=_TIMEOUT,
            )
            seen["settled_at"] = clock.now
            seen["evidence"] = handoff.request_evidence(unanswered)
            seen["proof"] = handoff.predecessor_serves(
                self.ctx,
                failure=unanswered,
                source_listener=self.source,
                source_runtime=captured,
                runtime_reader=self.health,
            )
            # The handler goes on: only now does it reach the begin of its handoff.
            return begin(
                server,
                expected,
                context,
                accepted_at=accepted_at,
                timeout_seconds=timeout_seconds,
                lease_seconds=lease_seconds,
            )

        mocker.patch.object(
            handoff_module, "prepare", side_effect=controller_settles_before_the_handler_goes_on
        )
        listener_control.prepare_handoff(handler, self.bindings, accepted_at=accepted_at)

        assert (seen["resolution"], seen["proof"]) == (("rolled_back", captured), True)
        assert seen["settled_at"] == accepted_at + _TIMEOUT + 1.0
        assert seen["evidence"] is handoff.RequestEvidence.EXPIRED
        handler.send_response.assert_called_once_with(408)
        assert json.loads(b"".join(written)) == {"ok": False, "error": "handoff_request_expired"}
        spawn.assert_not_called()
        assert self.health() == captured

    def test_a_handler_that_begins_at_its_last_moment_is_seen_before_the_controller_settles(
        self, *, mocker: MockerFixture
    ) -> None:
        """The other side of the bound: the latest begin that the listener admits.

        The handler begins when the clock reads the accept's stamp plus the timeout, right
        after a health read at that reading. The controller's next read shows the request's
        transaction, so it follows the handoff instead of settling a second later, and
        resolves when the handoff has rolled back.
        """
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        clock = PolledClock(mocker)
        accepted_at = clock.now
        captured = self.health()
        handler, written = self.handler(mocker)
        child = fake_child(mocker=mocker)
        child.recv_message.return_value = child_message("ready", child, expected_metadata())
        mocker.patch.object(handoff_module, "spawn_child", return_value=child)
        commit = mocker.patch("threading.Thread")
        unanswered = errors.InstallError("handoff control is unavailable")
        sessions: dict[float, object] = {}

        def read_then_let_the_listener_go_on(
            _ctx: runtime_context.RuntimeContext,
        ) -> dict[str, object]:
            snapshot = self.health()
            sessions[clock.now] = snapshot["handoff_state"]
            if clock.now == accepted_at + _TIMEOUT:
                listener_control.prepare_handoff(handler, self.bindings, accepted_at=accepted_at)
            elif clock.now == accepted_at + _TIMEOUT + 2.0:
                # The handoff fails before its successor commits, and the listener resumes.
                handoff_module._reset_preparation(child)
            return snapshot

        resolution = handoff.resolve_after_controller_failure(
            self.ctx,
            captured,
            expected_metadata(),
            failure=unanswered,
            source_listener=self.source,
            runtime_reader=read_then_let_the_listener_go_on,
            timeout_seconds=_TIMEOUT,
            lease_seconds=_TIMEOUT,
        )

        handler.send_response.assert_called_once_with(202)
        assert json.loads(b"".join(written))["state"] == "ready"
        commit.return_value.start.assert_called_once_with()
        assert sessions[accepted_at + _TIMEOUT] == "idle"
        assert sessions[accepted_at + _TIMEOUT + PolledClock.POLL_SECONDS] == "ready"
        # The wait would have been over at 31 seconds; the handoff was in flight then.
        assert sessions[accepted_at + _TIMEOUT + 1.0] == "ready"
        assert resolution == ("rolled_back", captured)
        assert clock.now == accepted_at + _TIMEOUT + 2.0 + PolledClock.POLL_SECONDS
        assert handoff.request_evidence(unanswered) is handoff.RequestEvidence.OBSERVED
