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
from tests.lifecycle.deployment.handoff.fixtures import PolledClock
from tests.lifecycle.deployment.handoff.fixtures import control_transport
from tests.lifecycle.deployment.handoff.fixtures import no_control_connection
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture
from tests.lifecycle.deployment.installation.fixtures import retained_identity
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import ready_ack

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# What ends a request without the listener's answer, and the failure that the command reports.
# Each test raises its own instance: a failure carries its evidence wherever it is raised again.
_NO_ANSWER = pytest.mark.parametrize(
    ("transport", "reported"),
    [
        (ConnectionResetError, errors.InstallError),
        (TimeoutError, errors.InstallError),
        (KeyboardInterrupt, KeyboardInterrupt),
    ],
    ids=["reset", "timeout", "interrupt"],
)
# The request carries the command's 30 seconds, and one more is the margin.
_WAIT_SECONDS = 31.0


class TestReleasedDeployment(ReleasedDeploymentFixture):
    @_NO_ANSWER
    def test_upgrade_rolls_back_when_an_idle_listener_never_answers_within_the_timeout(
        self,
        transport: type[BaseException],
        reported: type[BaseException],
        *,
        mocker: MockerFixture,
    ) -> None:
        """An idle listener may have read the request and be about to begin it, for a time.

        It begins a request only within the request's timeout of accepting it. It never shows
        the request and is at rest when that time has passed since its first answer, so it
        will begin nothing: the selection is restored and nothing is left to ``recover``.
        """
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        sent = control_transport(mocker, side_effect=transport())
        clock = PolledClock(mocker)
        health = mocker.Mock(return_value=current)

        with pytest.raises(reported) as raised:
            self.deploy(payload, current, adapter=service, runtime_reader=health, mocker=mocker)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", "rollback"]
        sent.assert_called_once()
        # The resolver's first read starts the wait, and its last one is the first after it.
        assert clock.now == _WAIT_SECONDS
        # Admission, the request and the proof read health once each; the resolver polls.
        assert health.call_count == 3 + clock.sleep.call_count + 1
        service.install_mock.assert_not_called()

    @_NO_ANSWER
    def test_rollback_rolls_back_when_an_idle_listener_never_answers_within_the_timeout(
        self,
        transport: type[BaseException],
        reported: type[BaseException],
        *,
        mocker: MockerFixture,
    ) -> None:
        """The worst case of the window: the retained release must not take over unseen.

        After the request's timeout at rest it cannot take over any more, and the selection of
        the release that serves is restored.
        """
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
        sent = control_transport(mocker, side_effect=transport())
        clock = PolledClock(mocker)

        with pytest.raises(reported) as raised:
            apply.rollback(self.ctx, retained, adapter=service, runtime_reader=lambda _ctx: current)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        # The transaction was not prepared any more, so closing a prepared one changes nothing.
        assert payload.events == ["commit", "activate", "rollback", "close_prepared"]
        sent.assert_called_once()
        assert clock.now == _WAIT_SECONDS
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
        sent = no_control_connection(mocker)
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

    def test_upgrade_rolls_back_at_once_when_the_connection_of_its_request_does_not_open(
        self, *, mocker: MockerFixture
    ) -> None:
        """A connect that fails wrote nothing, so the listener at rest proves the predecessor."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        sent = control_transport(mocker)
        connect = mocker.patch.object(
            apply.handoff.loopback,
            "connect_reset_on_close",
            side_effect=ConnectionRefusedError("refused"),
        )
        sleep = mocker.patch.object(
            apply.handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )
        # Admission, the request's check, the resolver and the proof.
        health = mocker.Mock(return_value=current)

        with pytest.raises(errors.InstallError, match="handoff control is unavailable") as raised:
            self.deploy(payload, current, adapter=service, runtime_reader=health, mocker=mocker)

        assert not isinstance(raised.value, apply.UnknownDeploymentOutcome)
        assert payload.events == ["commit", "activate", "rollback"]
        assert health.call_count == 4
        connect.assert_called_once()
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
        control_transport(mocker, return_value=Response(ready_ack(expected)))
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
        control_transport(mocker, side_effect=ConnectionResetError("reset"))
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
