"""A handoff request without the listener's answer, through upgrade and rollback.

The request, the resolver and the proof are the product's own; only the loopback transport,
the health reads and the clock are scripted.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture
from tests.lifecycle.deployment.installation.fixtures import retained_identity
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import ready_ack

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

_UNCONFIRMED = "handoff outcome is unconfirmed; transaction preserved for recovery"
_NO_ANSWER = pytest.mark.parametrize(
    "transport",
    [ConnectionResetError("reset"), TimeoutError("timed out"), KeyboardInterrupt()],
    ids=["reset", "timeout", "interrupt"],
)


def _waiting_clock(mocker: MockerFixture) -> MockType:
    """Advance the controller's clock by 100 seconds at each reading; return its sleep."""
    mocker.patch.object(
        apply.handoff.time, "monotonic", side_effect=itertools.count(0.0, 100.0).__next__
    )
    return mocker.patch.object(apply.handoff.time, "sleep")


class TestReleasedDeployment(ReleasedDeploymentFixture):
    @_NO_ANSWER
    def test_upgrade_preserves_its_transaction_when_an_idle_listener_never_answers(
        self, transport: BaseException, *, mocker: MockerFixture
    ) -> None:
        """An idle listener may have read the request and be about to begin it.

        It never shows the request, so nothing says that it will not begin: the selection is
        not restored and the transaction is left to ``recover``.
        """
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        sent = mocker.patch.object(apply.handoff.loopback, "open_request", side_effect=transport)
        sleep = _waiting_clock(mocker)
        health = mocker.Mock(return_value=current)

        with pytest.raises(BaseException, match=_UNCONFIRMED) as raised:
            self.deploy(payload, current, adapter=service, runtime_reader=health, mocker=mocker)

        assert isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", ("preserve", _UNCONFIRMED)]
        sent.assert_called_once()
        # Admission and the request read health once each; every later read is a poll.
        assert sleep.call_count == health.call_count - 2 > 0
        service.install_mock.assert_not_called()

    @_NO_ANSWER
    def test_rollback_preserves_its_transaction_when_an_idle_listener_never_answers(
        self, transport: BaseException, *, mocker: MockerFixture
    ) -> None:
        """The worst case of the window: the retained release must not take over unseen."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(release="1.2.3")
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        mocker.patch.object(generation, "control_context", return_value=self.ctx)
        sent = mocker.patch.object(apply.handoff.loopback, "open_request", side_effect=transport)
        sleep = _waiting_clock(mocker)

        with pytest.raises(BaseException, match=_UNCONFIRMED) as raised:
            apply.rollback(self.ctx, retained, adapter=service, runtime_reader=lambda _ctx: current)

        assert isinstance(raised.value, apply.UnknownDeploymentOutcome)
        # The transaction was not prepared any more, so closing a prepared one changes nothing.
        assert payload.events == [
            "commit",
            "activate",
            ("preserve", _UNCONFIRMED),
            "close_prepared",
        ]
        sent.assert_called_once()
        assert sleep.call_count > 0
        service.install_mock.assert_not_called()

    def test_upgrade_rolls_back_at_once_when_its_request_was_never_sent(
        self, *, mocker: MockerFixture
    ) -> None:
        """A health read that fails once before the send costs no recovery.

        The request finds the captured listener inactive and sends nothing, so nothing of it
        can begin: the listener that is idle again proves the predecessor, as at 6.0.1.
        """
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        sent = mocker.patch.object(apply.handoff.loopback, "open_request")
        sleep = mocker.patch.object(
            apply.handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )
        # Admission; the request's check, which no listener answers; the resolver; the proof.
        health = mocker.Mock(side_effect=[current, None, current, current])

        with pytest.raises(errors.InstallError, match="is no longer active") as raised:
            self.deploy(payload, current, adapter=service, runtime_reader=health, mocker=mocker)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", "rollback"]
        assert health.call_count == 4
        sent.assert_not_called()
        sleep.assert_not_called()
        service.install_mock.assert_not_called()

    def test_upgrade_rolls_back_an_acknowledged_handoff_that_the_listener_rolled_back(
        self, *, mocker: MockerFixture
    ) -> None:
        """The READY answer reaches the resolver and the proof on the failure itself."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        expected: dict[str, object] = {**payload.expected}
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply.handoff.loopback, "open_request", return_value=Response(ready_ack(expected))
        )
        mocker.patch.object(process, "wait_for_executable", return_value=None)
        sleep = mocker.patch.object(
            apply.handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )

        with pytest.raises(errors.InstallError, match="unverified successor") as raised:
            self.deploy(payload, current, mocker=mocker)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", "rollback"]
        sleep.assert_not_called()

    def test_upgrade_rolls_back_an_unanswered_handoff_that_it_saw_begin_and_end(
        self, *, mocker: MockerFixture
    ) -> None:
        """The resolver's observation reaches the proof on the failure itself."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        begun = self.current_runtime(
            handoff_state="preparing", handoff_transaction_id=payload.expected["transaction_id"]
        )
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply.handoff.loopback, "open_request", side_effect=ConnectionResetError("reset")
        )
        mocker.patch.object(
            apply.handoff.time, "monotonic", side_effect=itertools.count(0.0, 0.01).__next__
        )
        sleep = mocker.patch.object(apply.handoff.time, "sleep")
        # Admission, the request, three polls of the resolver, and the proof.
        health = mocker.Mock(side_effect=[current, current, current, begun, current, current])

        with pytest.raises(errors.InstallError, match="handoff control is unavailable") as raised:
            self.deploy(payload, current, runtime_reader=health, mocker=mocker)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", "rollback"]
        assert health.call_count == 6
        assert sleep.call_count == 2
