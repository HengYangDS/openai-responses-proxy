"""The connection of a handoff request opens in a step of its own, before any byte is written.

A failure of that step leaves the listener nothing to begin: it carries that nothing was sent,
and the resolver and the proof settle the request at once with the listener at rest. The
option that makes each close of the connection a reset belongs to that step, because a
request that cannot be withdrawn is not sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import control_transport
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

_RESTING = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
_UNSENT = handoff.RequestEvidence.UNSENT


class TestTheConnectOfAHandoffRequest:
    @pytest.fixture(autouse=True)
    def _capture_listener(self, tmp_path: Path, mocker: MockerFixture) -> None:
        self.ctx = install_context(tmp_path)
        self.source = process.OwnedProcess(999, self.ctx.executable, 0.5)
        self.mocker = mocker
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        self.sent = control_transport(mocker)

    def _ends(self, connect: BaseException, raised: type[BaseException]) -> BaseException:
        """Return what ends a real request whose connection fails to open with ``connect``."""
        self.mocker.patch.object(handoff.loopback, "connect_reset_on_close", side_effect=connect)
        with pytest.raises(raised) as ended:
            handoff.request(
                self.ctx,
                expected_metadata(),
                source_listener=self.source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
                timeout_seconds=1,
                lease_seconds=1,
            )
        return ended.value

    def test_a_failure_of_the_connect_carries_that_nothing_was_sent(
        self, subtests: pytest.Subtests
    ) -> None:
        """Refused, reset, timed out, or the option refused: one public failure, unsent."""
        for transport in (
            ConnectionRefusedError("refused"),
            ConnectionResetError("reset"),
            TimeoutError("timed out"),
            OSError("the option is not supported"),
        ):
            with subtests.test(point=str(transport)):
                failure = self._ends(transport, errors.InstallError)
                assert str(failure) == "handoff control is unavailable"
                assert failure.__cause__ is transport
                assert handoff.request_evidence(failure) is _UNSENT
        with subtests.test(point="an interrupt during the connect"):
            failure = self._ends(KeyboardInterrupt(), KeyboardInterrupt)
            assert handoff.request_evidence(failure) is _UNSENT
        self.sent.assert_not_called()

    def test_a_request_whose_connect_failed_is_settled_at_once_with_the_listener_at_rest(
        self,
    ) -> None:
        """Row "unsent", from the connect: the resolver and the proof read it without a wait."""
        failure = self._ends(ConnectionRefusedError("refused"), errors.InstallError)
        sleep = self.mocker.patch.object(
            handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )

        for captured, shown in (
            (idle_runtime(), idle_runtime()),
            (_RESTING, dict(_RESTING)),
            (_RESTING, idle_runtime()),
        ):
            assert handoff.resolve_after_controller_failure(
                self.ctx,
                captured,
                expected_metadata(),
                failure=failure,
                source_listener=self.source,
                runtime_reader=lambda _ctx, shown=shown: shown,
                timeout_seconds=1,
                lease_seconds=1,
            ) == ("rolled_back", shown)
            assert handoff.predecessor_serves(
                self.ctx,
                failure=failure,
                source_listener=self.source,
                source_runtime=captured,
                runtime_reader=lambda _ctx, shown=shown: shown,
            )
        # A handoff in flight is not at rest, with this evidence as with any.
        in_flight = idle_runtime(handoff_state="preparing", handoff_transaction_id="8" * 32)
        assert not handoff.predecessor_serves(
            self.ctx,
            failure=failure,
            source_listener=self.source,
            source_runtime=idle_runtime(),
            runtime_reader=lambda _ctx: in_flight,
        )
        sleep.assert_not_called()
        self.sent.assert_not_called()
