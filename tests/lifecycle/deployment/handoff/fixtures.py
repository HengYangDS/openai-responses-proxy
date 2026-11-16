"""Captured generations and temporary installation state for handoff contracts."""

from __future__ import annotations

import tempfile
from contextlib import ExitStack
from typing import TYPE_CHECKING

from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

    from openai_responses_proxy.lifecycle import context as runtime_context


def admit_handoff_generations(
    mocker: MockerFixture,
    ctx: runtime_context.RuntimeContext,
    *,
    predecessor_pid: int = 999,
    successor_pid: int = 1000,
) -> tuple[process.OwnedProcess, process.OwnedProcess, MockType]:
    """Admit exact predecessor and successor generations for controller tests."""
    predecessor = process.OwnedProcess(predecessor_pid, ctx.executable, 0.5)
    successor = process.OwnedProcess(successor_pid, ctx.executable, 1.0)
    mocker.patch.object(process, "wait_for_executable", return_value=successor)

    predecessor_checks = iter((True,))

    def generation_is_alive(generation: process.OwnedProcess) -> bool:
        if generation.pid == successor_pid:
            return True
        return next(predecessor_checks, False)

    alive = mocker.patch.object(
        process,
        "owned_process_alive",
        side_effect=generation_is_alive,
    )
    mocker.patch.object(process, "listener_pids", return_value=[predecessor_pid])
    return predecessor, successor, alive


_NO_ANSWER = object()


def control_transport(
    mocker: MockerFixture, *, return_value: object = _NO_ANSWER, side_effect: object = None
) -> MockType:
    """Open the connection of each handoff request; return the call that sends the request.

    What that call returns or raises is the listener's answer, or the failure of the sending
    or of the reading. Its second argument is the request.
    """
    mocker.patch.object(
        handoff.loopback, "connect_reset_on_close", return_value=mocker.sentinel.connection
    )
    return mocker.patch.object(
        handoff.loopback,
        "open_request_on",
        return_value=mocker.DEFAULT if return_value is _NO_ANSWER else return_value,
        side_effect=side_effect,
    )


def no_control_connection(mocker: MockerFixture) -> MockType:
    """Fail a handoff request that opens its connection; return the call that would open it."""
    return mocker.patch.object(
        handoff.loopback,
        "connect_reset_on_close",
        side_effect=AssertionError("a handoff request opened its connection"),
    )


class PolledClock:
    """A controller's clock that moves only while the controller waits between two polls.

    Each wait takes an eighth of a second, whatever it asks for. The sums are exact, so a test
    names the reading at which a wait is over instead of counting readings of the clock.
    """

    POLL_SECONDS = 0.125

    def __init__(self, mocker: MockerFixture) -> None:
        """Replace the controller's clock and its sleep for the test."""
        self.now = 0.0
        mocker.patch.object(handoff.time, "monotonic", side_effect=lambda: self.now)
        self.sleep = mocker.patch.object(handoff.time, "sleep", side_effect=self._wait)

    def _wait(self, _seconds: float) -> None:
        self.now += self.POLL_SECONDS


class HandoffFixture:
    """Release each controller contract's temporary installation state."""

    def setup_method(self) -> None:
        self._cleanups = ExitStack()
        self.tempdir = tempfile.TemporaryDirectory()
        self._cleanups.callback(self.tempdir.cleanup)

    def teardown_method(self) -> None:
        self._cleanups.close()
