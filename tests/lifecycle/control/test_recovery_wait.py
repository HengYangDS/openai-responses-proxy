"""The wait that ``recover`` takes before it restores a transaction that reached activation.

A listener begins a handoff request only within the request's timeout of accepting its
connection, and it accepted that connection before the connection of any later health read.
``recover`` does not know the request that its transaction may have left pending, so it waits
the longest timeout that a request can carry and the margin, from the return of its first
read to the asking of its second.

The clock moves an eighth of a second at each sleep and stands still otherwise: no thread,
socket, process or real clock decides an order.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING
from typing import override

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.serving import control as listener_control
from tests.lifecycle.deployment.handoff.fixtures import PolledClock
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.transaction.fixtures import InterruptedTransition
from tests.lifecycle.transaction.fixtures import interrupted_transition
from tests.lifecycle.transaction.fixtures import observed_installation
from tests.serving.handoff.fixtures import child_message
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_child
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import handoff_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context

# Step 2's owner: the longest timeout that a request can carry, and the margin of the wait.
_LONGEST = handoff.carried_timeout_seconds(sys.maxsize) + handoff._EXPIRY_MARGIN_SECONDS
_NOT_AT_REST = "payload recovery runtime does not match the prior selected generation"


class WaitFixture:
    """One interrupted upgrade, a supervisor that binds what it is given, and a patched clock."""

    @pytest.fixture(autouse=True)
    def _interrupted_upgrade(self, tmp_path: Path, mocker: MockerFixture) -> Iterator[None]:
        self.ctx = install_context(tmp_path)
        self.interrupted = self.transition(mocker)
        self.native = mocker.patch.object(control, "adapter").return_value
        self.native.configured_executable.side_effect = lambda selected: selected.executable
        self.pending = observed_installation(self.ctx)
        self.clock = PolledClock(mocker)
        handoff_module.reset_session_to_idle()
        yield
        handoff_module.reset_session_to_idle()

    def transition(self, mocker: MockerFixture) -> InterruptedTransition:
        return interrupted_transition(self.ctx, mocker=mocker, preserved=True)


class TestTheWait(WaitFixture):
    def test_it_runs_from_the_return_of_the_first_read_to_the_asking_of_the_second(
        self, *, mocker: MockerFixture
    ) -> None:
        asked: list[float] = []
        installation: list[object] = []

        def a_read_that_takes_two_seconds(
            _ctx: runtime_context.RuntimeContext,
        ) -> dict[str, object]:
            asked.append(self.clock.now)
            installation.append(observed_installation(self.ctx))
            self.clock.now += 2.0
            return self.interrupted.at_rest

        mocker.patch.object(control, "read_runtime", side_effect=a_read_that_takes_two_seconds)

        result = control.recover(self.ctx)

        first_returned = 2.0
        assert asked == [0.0, first_returned + _LONGEST]
        # Nothing was restored before the second read was asked.
        assert installation == [self.pending, self.pending]
        # The first sleep asks for all of it, and the clock decides when it is over.
        assert self.clock.sleep.call_args_list[0].args == (_LONGEST,)
        assert result["state"] == "rolled_back"
        assert payload_generation.read(self.ctx) == self.interrupted.prior
        self.native.install.assert_called_once_with(self.interrupted.prior_context)

    def test_it_is_the_longest_timeout_that_a_request_can_carry_and_the_margin(self) -> None:
        assert _LONGEST == 121.0
        assert handoff.LONGEST_WAIT_AT_REST_SECONDS == _LONGEST

    def test_it_is_announced_once_before_it_begins(self, *, mocker: MockerFixture) -> None:
        read = mocker.patch.object(control, "read_runtime", return_value=self.interrupted.at_rest)
        announced: list[tuple[float, int, int]] = []

        def announce(seconds: float) -> None:
            announced.append((seconds, read.call_count, self.clock.sleep.call_count))

        assert control.recover(self.ctx, announce_wait=announce)["state"] == "rolled_back"

        # After the first read, before any sleep and before the second read.
        assert announced == [(_LONGEST, 1, 0)]
        assert read.call_count == 2

    def test_an_interrupt_leaves_the_journal_and_the_selection_as_they_were(
        self, *, mocker: MockerFixture
    ) -> None:
        read = mocker.patch.object(control, "read_runtime", return_value=self.interrupted.at_rest)
        self.clock.sleep.side_effect = KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            control.recover(self.ctx)

        assert observed_installation(self.ctx) == self.pending
        read.assert_called_once_with(self.ctx)
        self.native.install.assert_not_called()


class TestNoWait(WaitFixture):
    @override
    def transition(self, mocker: MockerFixture) -> InterruptedTransition:
        return interrupted_transition(self.ctx, mocker=mocker, reached="materialized")

    def test_a_transaction_that_did_not_reach_activation_is_restored_at_once(
        self, *, mocker: MockerFixture
    ) -> None:
        read = mocker.patch.object(control, "read_runtime", return_value=self.interrupted.at_rest)
        announce = mocker.Mock()

        assert control.recover(self.ctx, announce_wait=announce)["state"] == "rolled_back"

        read.assert_called_once_with(self.ctx)
        self.clock.sleep.assert_not_called()
        announce.assert_not_called()
        assert payload_generation.read(self.ctx) == self.interrupted.prior


# The longest timeout that a request can carry: the listener may begin it until then.
_TIMEOUT = 120
_REQUEST = {**expected_metadata(), "timeout_seconds": _TIMEOUT}


class TestTheWindowFromBothSides(WaitFixture):
    """The listener's own handler holds a request that it has received while ``recover`` runs.

    Its controller is gone. The listener accepted the request's connection and answers the
    first health read of ``recover`` at the same reading of the clock.
    """

    def listen(self, mocker: MockerFixture) -> None:
        """Give the prior generation's listener its own handler and session."""
        context = entrypoint_module._handoff_context()
        object.__setattr__(context, "successor_executable", lambda: context.executable)
        self.bindings = listener_control.Bindings(
            runtime_status=entrypoint_module.runtime_status,
            handoff_context=lambda: context,
        )
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        self.handler = fake_handler(_REQUEST, mocker=mocker)
        self.written: list[bytes] = []
        self.handler.wfile.write.side_effect = self.written.append
        self.sessions: dict[float, object] = {}
        mocker.patch.object(control, "read_runtime", side_effect=self.health)

    def health(self, _ctx: runtime_context.RuntimeContext) -> dict[str, object]:
        """Read the prior generation's listener with its own session, as health shows it."""
        session = handoff_module.runtime_identity(self.bindings.handoff_context())
        self.sessions[self.clock.now] = session["handoff_state"]
        return {
            **self.interrupted.at_rest,
            "handoff_state": session["handoff_state"],
            "handoff_transaction_id": session["handoff_transaction_id"],
        }

    def test_a_handler_that_reaches_its_begin_after_recovery_restored_is_refused(
        self, *, mocker: MockerFixture
    ) -> None:
        """The handler stands before its begin while ``recover`` waits, reads and restores."""
        self.listen(mocker)
        accepted_at = self.clock.now
        spawn = mocker.patch.object(handoff_module, "spawn_child")
        begin = handoff_module.prepare
        seen: dict[str, object] = {}

        def recovery_restores_before_the_handler_goes_on(
            server: ThreadingHTTPServer,
            expected: dict[str, object],
            context: handoff_module.Context,
            *,
            accepted_at: float | None,
            timeout_seconds: float,
            lease_seconds: float,
        ) -> handoff_module.PreparedHandoff:
            seen["result"] = control.recover(self.ctx)
            seen["restored_at"] = self.clock.now
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
            handoff_module, "prepare", side_effect=recovery_restores_before_the_handler_goes_on
        )
        listener_control.prepare_handoff(self.handler, self.bindings, accepted_at=accepted_at)

        assert seen["restored_at"] == accepted_at + _TIMEOUT + 1.0
        assert self.sessions == {accepted_at: "idle", accepted_at + _TIMEOUT + 1.0: "idle"}
        assert isinstance(seen["result"], dict)
        assert seen["result"]["state"] == "rolled_back"
        assert payload_generation.read(self.ctx) == self.interrupted.prior
        self.handler.send_response.assert_called_once_with(408)
        assert json.loads(b"".join(self.written)) == {
            "ok": False,
            "error": "handoff_request_expired",
        }
        spawn.assert_not_called()

    def test_a_handler_that_begins_at_its_last_moment_is_seen_after_the_wait(
        self, *, mocker: MockerFixture
    ) -> None:
        """The latest begin that the listener admits falls inside the wait: nothing is restored."""
        self.listen(mocker)
        accepted_at = self.clock.now
        child = fake_child(mocker=mocker)
        child.recv_message.return_value = child_message("ready", child, expected_metadata())
        mocker.patch.object(handoff_module, "spawn_child", return_value=child)
        commit = mocker.patch("threading.Thread")

        def wait_and_let_the_listener_go_on(_seconds: float) -> None:
            self.clock.now += PolledClock.POLL_SECONDS
            if self.clock.now == accepted_at + _TIMEOUT:
                listener_control.prepare_handoff(
                    self.handler, self.bindings, accepted_at=accepted_at
                )

        self.clock.sleep.side_effect = wait_and_let_the_listener_go_on

        with pytest.raises(errors.RecoveryStateError, match=_NOT_AT_REST):
            control.recover(self.ctx)

        self.handler.send_response.assert_called_once_with(202)
        assert json.loads(b"".join(self.written))["state"] == "ready"
        commit.return_value.start.assert_called_once_with()
        assert self.sessions == {accepted_at: "idle", accepted_at + _TIMEOUT + 1.0: "ready"}
        assert observed_installation(self.ctx) == self.pending
        self.native.install.assert_not_called()
