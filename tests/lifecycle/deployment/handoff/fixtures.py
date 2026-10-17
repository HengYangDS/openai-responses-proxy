"""Captured generations and temporary installation state for handoff contracts."""

from __future__ import annotations

import tempfile
from contextlib import ExitStack

from openai_responses_proxy.lifecycle.supervision import process


def admit_handoff_generations(
    mocker,
    ctx,
    *,
    predecessor_pid: int = 999,
    successor_pid: int = 1000,
):
    """Admit exact predecessor and successor generations for controller tests."""
    predecessor = process.OwnedProcess(predecessor_pid, ctx.executable, 0.5)
    successor = process.OwnedProcess(successor_pid, ctx.executable, 1.0)
    mocker.patch.object(process, "wait_for_executable", return_value=successor)

    predecessor_checks = iter((True,))

    def generation_is_alive(generation):
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


class HandoffFixture:
    """Release each controller contract's temporary installation state."""

    def setup_method(self):
        self._cleanups = ExitStack()
        self.tempdir = tempfile.TemporaryDirectory()
        self._cleanups.callback(self.tempdir.cleanup)

    def teardown_method(self) -> None:
        self._cleanups.close()
