"""A refusal is taken whole before its connection is left, and its status alone is the evidence.

Leaving the connection of a handoff request resets it, and a listener writes the status of a
refusal and then its body. The controller reads the body to its end, within the bound of a
reply in bytes, and leaves the connection whatever ends that reading. What the status has
said stays said: a body that breaks off, or an interrupt of its reading, changes nothing of it.
"""

from __future__ import annotations

import http.client
import io
import urllib.error
from email.message import Message
from typing import TYPE_CHECKING
from typing import override

import pytest

from openai_responses_proxy.lifecycle.deployment import handoff
from tests.lifecycle.deployment.handoff.fixtures import control_transport
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import expected_metadata

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

_REFUSAL = b'{"ok":false,"error":"handoff_in_progress"}'
_REFUSED = handoff.RequestEvidence.REFUSED


class _RecordedBody(io.BytesIO):
    """The body of a refusal that records how much of it had been read when it was left."""

    def __init__(self, content: bytes, *, ends_with: BaseException | None = None) -> None:
        super().__init__(content)
        self._ends_with = ends_with
        self.read_when_left: int | None = None

    @override
    def read(self, size: int | None = -1, /) -> bytes:
        if self._ends_with is not None:
            raise self._ends_with
        return super().read(size)

    @override
    def close(self) -> None:
        if not self.closed and self.read_when_left is None:
            self.read_when_left = self.tell()
        super().close()


class TestTheLeavingOfARefusal:
    @pytest.fixture(autouse=True)
    def _controller(self, tmp_path: Path, mocker: MockerFixture) -> None:
        self.ctx = install_context(tmp_path)
        self.sent = control_transport(mocker)

    def _refused(
        self, status: int, body: _RecordedBody, raised: type[BaseException]
    ) -> BaseException:
        """Return what ends a request that the listener refuses with ``status`` and ``body``."""
        self.sent.side_effect = urllib.error.HTTPError("url", status, "refused", Message(), body)
        with pytest.raises(raised) as ended:
            handoff.post_ready(self.ctx, expected_metadata(), lease_seconds=1, timeout_seconds=1)
        return ended.value

    def test_a_refusal_is_taken_whole_before_the_connection_is_left(self) -> None:
        """Leaving the connection resets it, so the listener must have written its whole answer.

        A listener writes the status of a refusal and then its body. On Linux a controller that
        left at the status reset the connection under the body in 3 of 15 refusals, and the
        listener logged a request that its client had closed.
        """
        body = _RecordedBody(_REFUSAL)

        failure = self._refused(409, body, handoff.HandoffRefusedError)

        assert str(failure) == "handoff control returned HTTP 409"
        assert body.closed
        assert body.read_when_left == len(_REFUSAL)

    def test_a_refusal_is_read_no_further_than_the_bound_of_a_reply(self) -> None:
        """One byte past the bound, as for an acknowledgement: a larger body is left there."""
        bound = handoff._MAX_BODY_BYTES
        body = _RecordedBody(b"r" * (bound + 2))

        failure = self._refused(503, body, handoff.HandoffRefusedError)

        assert handoff.request_evidence(failure) is _REFUSED
        assert body.read_when_left == bound + 1

    def test_a_refusal_whose_body_breaks_off_is_still_the_listeners_refusal(
        self, subtests: pytest.Subtests
    ) -> None:
        """The status has said that the listener refused; the rest of the answer adds nothing."""
        for ending in (
            ConnectionResetError("the body broke off"),
            TimeoutError("the body did not come"),
            http.client.IncompleteRead(b"", 42),
            ValueError("the answer was closed"),
        ):
            with subtests.test(ending=type(ending).__name__):
                body = _RecordedBody(_REFUSAL, ends_with=ending)

                failure = self._refused(503, body, handoff.HandoffRefusedError)

                assert str(failure) == "handoff control returned HTTP 503"
                assert handoff.request_evidence(failure) is _REFUSED
                assert body.closed

    def test_an_interrupt_of_the_reading_leaves_the_connection_and_carries_the_refusal(
        self,
    ) -> None:
        """The status was read before the interrupt: the resolver need not wait the request out."""
        interrupt = KeyboardInterrupt()
        body = _RecordedBody(_REFUSAL, ends_with=interrupt)

        failure = self._refused(409, body, KeyboardInterrupt)

        assert failure is interrupt
        assert body.closed
        assert handoff.request_evidence(failure) is _REFUSED
