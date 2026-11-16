"""The connection of a handoff request opens in a step of its own, before any byte is written.

A failure of that step leaves the listener nothing to begin: it carries that nothing was sent,
and the resolver and the proof settle the request at once with the listener at rest. The
option that makes each close of the connection a reset belongs to that step, because a
request that cannot be withdrawn is not sent.
"""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from typing import TYPE_CHECKING
from typing import override

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
_REFUSAL = b'{"ok":false,"error":"handoff_in_progress"}'


class _RecordedBody(io.BytesIO):
    """The body of a refusal that records how much of it had been read when it was left."""

    def __init__(self, content: bytes, *, breaks_off: bool = False) -> None:
        super().__init__(content)
        self._breaks_off = breaks_off
        self.read_when_left: int | None = None

    @override
    def read(self, size: int | None = -1, /) -> bytes:
        if self._breaks_off:
            raise ConnectionResetError("the body broke off")
        return super().read(size)

    @override
    def close(self) -> None:
        if not self.closed and self.read_when_left is None:
            self.read_when_left = self.tell()
        super().close()


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

    def test_the_request_is_sent_on_the_connection_that_was_opened_for_it(self) -> None:
        """One connection: opened with the request's timeout and the transport's second, sent on."""
        opened = self.mocker.patch.object(
            handoff.loopback, "connect_reset_on_close", return_value=self.mocker.sentinel.opened
        )
        self.sent.side_effect = ConnectionResetError("reset")

        with pytest.raises(errors.InstallError, match="handoff control is unavailable"):
            handoff.post_ready(self.ctx, expected_metadata(), lease_seconds=1, timeout_seconds=7)

        [opening] = opened.call_args_list
        [sending] = self.sent.call_args_list
        [request] = opening.args
        assert opening.kwargs == {"timeout_seconds": 8.0}
        assert sending.args == (self.mocker.sentinel.opened, request)
        assert sending.kwargs == {}

    def test_a_refusal_is_taken_whole_before_the_connection_is_left(self) -> None:
        """Leaving the connection resets it, so the listener must have written its whole answer.

        A listener writes the status of a refusal and then its body. On Linux a controller that
        left at the status reset the connection under the body in 3 of 15 refusals, and the
        listener logged a request that its client had closed.
        """
        body = _RecordedBody(_REFUSAL)
        self.sent.side_effect = urllib.error.HTTPError("url", 409, "conflict", Message(), body)

        with pytest.raises(handoff.HandoffRefusedError, match="HTTP 409"):
            handoff.post_ready(self.ctx, expected_metadata(), lease_seconds=1, timeout_seconds=1)

        assert body.closed
        assert body.read_when_left == len(_REFUSAL)

    def test_a_refusal_whose_body_breaks_off_is_still_the_listeners_refusal(self) -> None:
        """The status has said that the listener refused; the rest of the answer adds nothing."""
        body = _RecordedBody(_REFUSAL, breaks_off=True)
        self.sent.side_effect = urllib.error.HTTPError("url", 503, "unavailable", Message(), body)

        with pytest.raises(handoff.HandoffRefusedError, match="HTTP 503") as refused:
            handoff.post_ready(self.ctx, expected_metadata(), lease_seconds=1, timeout_seconds=1)

        assert handoff.request_evidence(refused.value) is handoff.RequestEvidence.REFUSED
        assert body.closed

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
