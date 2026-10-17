"""Published predecessor native-generation replacement."""

from __future__ import annotations

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import OrderedServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_finalized_legacy_runtime_uses_one_bounded_native_generation_replacement(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(
            handoff_capabilities=["repeatable"],
            handoff_state="finalized",
            handoff_transaction_id="txn-previous",
        )
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        capture = mocker.patch.object(process, "capture_executable", return_value=source)
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        wait = mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)
        request = mocker.patch.object(apply, "request_handoff")

        result = self.deploy(payload, current, adapter=service, mocker=mocker)

        assert result == {"state": "upgraded", "runtime": runtime}
        assert payload.events == ["commit", "activate", ("finalize", runtime)]
        capture.assert_called_once_with(
            111,
            self.ctx.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
        terminate.assert_called_once_with(source, timeout_seconds=30)
        drain.assert_called_once_with(
            payload.context,
            source_listener=source,
            source_runtime=current,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
        )
        wait.assert_called_once_with(
            payload.context,
            payload.expected,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
            old_pid=111,
        )
        request.assert_not_called()
        service.install_mock.assert_called_once_with(payload.context)

    @pytest.mark.parametrize("failure_phase", ["supervisor", "serving"])
    @pytest.mark.parametrize("failure_type", [errors.InstallError, KeyboardInterrupt])
    def test_native_replacement_preserves_unconfirmed_successor(
        self, failure_phase: str, failure_type: type[BaseException], *, mocker
    ) -> None:
        """Keep durable recovery authority after the predecessor has exited."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(handoff_capabilities=["repeatable"])
        failure = failure_type("successor unavailable")
        service = FakeServiceAdapter(
            failure=failure if failure_phase == "supervisor" else None,
            mocker=mocker,
        )
        mocker.patch.object(process, "terminate_owned_process", return_value=True)
        mocker.patch.object(apply.handoff, "drain_responses")
        wait = mocker.patch.object(apply, "wait_for_serving_runtime", side_effect=failure)
        resume = mocker.patch.object(apply.handoff, "resume_responses")

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="native successor startup is unconfirmed",
        ) as raised:
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == ["commit", "activate", ("preserve", str(raised.value))]
        service.install_mock.assert_called_once_with(payload.context)
        assert wait.call_count == (1 if failure_phase == "serving" else 0)
        resume.assert_not_called()

    def test_published_predecessor_without_selected_generation_handoff_uses_replacement(
        self, *, mocker
    ) -> None:
        """Do not ask an older listener to launch a payload outside its own root."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(handoff_capabilities=["repeatable"])
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(process, "capture_executable", return_value=source)
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        wait = mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)
        request = mocker.patch.object(apply, "request_handoff")

        result = self.deploy(payload, current, adapter=service, mocker=mocker)

        assert result == {"state": "upgraded", "runtime": runtime}
        drain.assert_called_once_with(
            payload.context,
            source_listener=source,
            source_runtime=current,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
        )
        terminate.assert_called_once_with(source, timeout_seconds=30)
        wait.assert_called_once_with(
            payload.context,
            payload.expected,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
            old_pid=111,
        )
        request.assert_not_called()

    def test_native_replacement_binds_current_supervisor_before_waiting_for_successor(
        self, *, mocker
    ) -> None:
        """The old watchdog need not understand the current transaction schema."""
        events: list[object] = []
        payload = FakeTransaction(self.ctx, events=events)
        current = self.current_runtime(
            handoff_capabilities=["repeatable"],
            handoff_state="finalized",
            handoff_transaction_id="txn-previous",
        )
        service = OrderedServiceAdapter(events, mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(process, "capture_executable", return_value=source)

        def terminate(*_args, **_kwargs):
            events.append("source-exit")
            return True

        mocker.patch.object(
            apply.handoff,
            "drain_responses",
            side_effect=lambda *_args, **_kwargs: events.append("source-drained"),
        )
        mocker.patch.object(process, "terminate_owned_process", side_effect=terminate)
        mocker.patch.object(
            apply,
            "wait_for_serving_runtime",
            side_effect=lambda *_args, **_kwargs: (
                events.append("successor-serving") or self.successor()
            ),
        )

        self.deploy(payload, current, adapter=service, mocker=mocker)

        assert events == [
            "commit",
            "source-drained",
            "activate",
            "source-exit",
            "service-install",
            "successor-serving",
            ("finalize", self.successor()),
        ]

    def test_generation_replacement_requires_exact_source_identity_before_write(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(
            handoff_capabilities=["repeatable"],
            handoff_state="finalized",
            handoff_transaction_id="txn-previous",
        )
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(process, "capture_executable", return_value=None)

        with pytest.raises(errors.InstallError, match="process generation"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == []
        service.install_mock.assert_not_called()

    @pytest.mark.parametrize("phase_failure", [False, True])
    def test_generation_replacement_preserves_an_unconfirmed_source_exit(
        self, phase_failure, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(
            handoff_capabilities=["repeatable"],
            handoff_state="finalized",
            handoff_transaction_id="txn-previous",
        )
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(process, "capture_executable", return_value=source)
        failure = "native process termination wait: access denied; exit is unproved"
        mocker.patch.object(
            process,
            "terminate_owned_process",
            side_effect=errors.InstallError(failure) if phase_failure else None,
            return_value=False,
        )
        mocker.patch.object(apply.handoff, "drain_responses")

        with pytest.raises(apply.UnknownDeploymentOutcome, match="generation replacement"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                "native generation replacement outcome is unconfirmed"
                + (f"; {failure}" if phase_failure else ""),
            ),
        ]
