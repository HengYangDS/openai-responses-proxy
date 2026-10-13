"""Released deployment orchestration contracts."""

from __future__ import annotations

import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import cast
from typing import override

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import transaction
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import roles as serving_roles
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact


class FakeTransaction:
    """Behavioral double for the payload transaction protocol."""

    def __init__(self, ctx, *, events: list[object] | None = None) -> None:
        """Initialize an observable payload-transaction double."""
        transaction_id = "1" * 32
        self.expected = {
            "transaction_id": transaction_id,
            "release": "1.2.3",
            "manifest_sha256": "b" * 64,
            "serving_payload_sha256": "c" * 64,
            "release_receipt_sha256": "d" * 64,
        }
        self.events = events if events is not None else []
        self.context = generation.context(ctx, transaction_id)

    def commit_projection(self) -> None:
        self.events.append("commit")

    def activate(self) -> None:
        self.events.append("activate")

    def finalize(self, runtime: dict[str, object] | None = None) -> None:
        self.events.append(("finalize", runtime))

    def rollback(self) -> None:
        self.events.append("rollback")

    def preserve_for_recovery(self, reason: str) -> None:
        self.events.append(("preserve", reason))


def as_transaction(value: FakeTransaction) -> transaction.PayloadTransaction:
    return cast("transaction.PayloadTransaction", value)


def retained_identity(release: str) -> identity.LoadedPayloadIdentity:
    """Build the identity surface needed by reverse deployment tests."""
    return identity.LoadedPayloadIdentity(
        release=release,
        serving_payload_sha256="a" * 64,
        release_receipt_sha256="b" * 64,
        manifest_sha256="c" * 64,
        root=Path("/retained") / release,
    )


class FakeServiceAdapter:
    """Record native-service calls and expose a configured executable."""

    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        configured: str | None = "canonical",
        mocker,
    ) -> None:
        """Initialize the adapter with an optional installation failure."""
        self.install_mock = mocker.Mock(side_effect=failure)
        self.uninstall_mock = mocker.Mock()
        self.configured = configured
        self.running_contexts: set[str] = set()

    def install(self, ctx) -> None:
        self.install_mock(ctx)
        self.configured = "canonical"
        self.running_contexts.add(ctx.executable)

    def uninstall(self, ctx) -> None:
        self.uninstall_mock(ctx)
        self.configured = None
        self.running_contexts.discard(ctx.executable)

    def runtime_pids(self, ctx) -> list[int]:
        return [222] if ctx.executable in self.running_contexts else []

    def terminate_runtime(self, ctx, *, timeout_seconds: float) -> int:
        del timeout_seconds
        running = self.runtime_pids(ctx)
        self.running_contexts.discard(ctx.executable)
        return len(running)

    def configured_executable(self, ctx) -> str | None:
        configured = ctx.executable if self.configured == "canonical" else self.configured
        return configured if isinstance(configured, str) else None

    def status(self, ctx) -> str:
        return "running" if ctx.executable in self.running_contexts else "absent"


class OrderedServiceAdapter(FakeServiceAdapter):
    """Record the service rebind in the same event stream as the transaction."""

    def __init__(self, events: list[object], *, mocker) -> None:
        super().__init__(mocker=mocker)
        self.events = events

    @override
    def install(self, ctx) -> None:
        self.events.append("service-install")
        super().install(ctx)

    @override
    def uninstall(self, ctx) -> None:
        self.events.append("service-uninstall")
        super().uninstall(ctx)


class TestReleasedDeployment:
    @pytest.fixture(autouse=True)
    def _admit_current_listener(self, tmp_path: Path, mocker) -> None:
        """Own the isolated install root and exact predecessor identity."""
        self.ctx = install_context(tmp_path)
        mocker.patch.object(
            process,
            "capture_executable",
            return_value=process.OwnedProcess(111, self.ctx.executable, 1.0),
        )
        mocker.patch.object(process, "listener_pids", return_value=[111])
        mocker.patch.object(process, "owned_process_alive", return_value=True)

    @staticmethod
    def current_runtime(**changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "pid": 111,
            "handoff_protocol_version": 2,
            "handoff_capabilities": [
                handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
            ],
            "handoff_state": "idle",
            "handoff_transaction_id": None,
            "release": "1.2.2",
            "serving_payload_sha256": "1" * 64,
            "payload_manifest_sha256": "2" * 64,
            "release_receipt_sha256": "3" * 64,
            "accepting": True,
            "draining": False,
        }
        value.update(changes)
        return value

    @staticmethod
    def successor(**changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "pid": 222,
            "release": "1.2.3",
            "serving_payload_sha256": "c" * 64,
            "payload_manifest_sha256": "b" * 64,
            "release_receipt_sha256": "d" * 64,
            "accepting": True,
            "draining": False,
        }
        value.update(changes)
        return value

    def deploy(
        self,
        payload: FakeTransaction,
        current: dict[str, object] | None,
        *,
        adapter: FakeServiceAdapter | None = None,
        timeout_seconds: float = 30,
        mocker,
    ) -> dict[str, object]:
        mocker.patch.object(
            generation,
            "control_context",
            side_effect=[self.ctx, payload.context],
        )
        return apply.install(
            self.ctx,
            as_transaction(payload),
            adapter=adapter or FakeServiceAdapter(mocker=mocker),
            runtime_reader=lambda _ctx: current,
            timeout_seconds=timeout_seconds,
        )

    def test_fresh_install_commits_and_finalizes_after_runtime_proof(self, *, mocker) -> None:
        events: list[object] = []
        payload = FakeTransaction(self.ctx, events=events)
        service = OrderedServiceAdapter(events, mocker=mocker)
        runtime = self.successor(pid=123)
        mocker.patch.object(process, "listener_pids", return_value=[])
        mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)

        result = self.deploy(payload, None, adapter=service, mocker=mocker)

        assert result == {"state": "installed", "runtime": runtime}
        assert payload.events == [
            "commit",
            "activate",
            "service-install",
            ("finalize", runtime),
        ]
        service.install_mock.assert_called_once_with(payload.context)

    def test_current_upgrade_rebinds_control_after_successor_handoff(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        capture = mocker.patch.object(process, "capture_executable", return_value=source)
        request = mocker.patch.object(
            apply,
            "request_handoff",
            return_value=runtime,
        )

        result = self.deploy(payload, current, adapter=service, mocker=mocker)

        assert result == {"state": "upgraded", "runtime": runtime}
        assert payload.events == ["commit", "activate", ("finalize", runtime)]
        capture.assert_called_once_with(
            111,
            self.ctx.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
        request.assert_called_once_with(
            payload.context,
            payload.expected,
            current=current,
            source_listener=source,
            runtime_reader=mocker.ANY,
            timeout_seconds=30,
        )
        service.uninstall_mock.assert_not_called()
        service.install_mock.assert_called_once_with(payload.context)

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

    def test_candidate_processes_exit_before_payload_rollback(self, *, mocker) -> None:
        events: list[object] = []
        payload = FakeTransaction(self.ctx, events=events)
        current = self.current_runtime()
        service = OrderedServiceAdapter(events, mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=errors.InstallError("successor unavailable"),
        )
        mocker.patch.object(apply.handoff, "resume_responses", return_value=True)

        def terminate(ctx, *, timeout_seconds):
            del timeout_seconds
            events.extend((("candidate-exit", 222), ("candidate-exit", 333)))
            service.running_contexts.discard(ctx.executable)
            return 2

        service.terminate_runtime = mocker.Mock(side_effect=terminate)

        with pytest.raises(errors.InstallError, match="successor unavailable"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert events == [
            "commit",
            "activate",
            "rollback",
        ]
        service.uninstall_mock.assert_not_called()
        service.install_mock.assert_not_called()
        service.terminate_runtime.assert_not_called()

    def test_current_upgrade_accepts_an_equivalent_supervisor_path(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        runtime = self.successor()
        service = FakeServiceAdapter(mocker=mocker)
        executable = Path(self.ctx.executable)
        candidate_executable = Path(payload.context.executable)
        service.configured_executable = mocker.Mock(
            side_effect=[
                str(executable.parent / ".." / executable.parent.name / executable.name),
                str(
                    candidate_executable.parent
                    / ".."
                    / candidate_executable.parent.name
                    / candidate_executable.name
                ),
            ]
        )
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)

        assert self.deploy(payload, current, adapter=service, mocker=mocker) == {
            "state": "upgraded",
            "runtime": runtime,
        }
        request.assert_called_once()

    def test_current_upgrade_refuses_an_unproved_supervisor_rebind(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        service.configured_executable = mocker.Mock(side_effect=[self.ctx.executable, None])
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        runtime = self.successor()
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="supervisor rebind failed",
        ):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                (
                    "successor committed but native supervisor rebind failed; "
                    "failure: native supervisor did not bind the lifecycle "
                    "control executable"
                ),
            ),
        ]
        service.install_mock.assert_called_once_with(payload.context)
        service.uninstall_mock.assert_not_called()
        request.assert_called_once()

    def test_supervisor_install_failure_preserves_the_committed_successor(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        service.install_mock.side_effect = errors.InstallError("service failed")
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        runtime = self.successor()
        mocker.patch.object(apply, "request_handoff", return_value=runtime)

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="supervisor rebind failed",
        ):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                "successor committed but native supervisor rebind failed; failure: service failed",
            ),
        ]
        service.install_mock.assert_called_once_with(payload.context)
        service.uninstall_mock.assert_not_called()

    def test_upgrade_requires_the_control_supervisor_before_payload_mutation(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(configured="/retired/launcher", mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        request = mocker.patch.object(apply, "request_handoff")

        with pytest.raises(errors.InstallError, match="lifecycle control executable"):
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert payload.events == []
        service.install_mock.assert_not_called()
        request.assert_not_called()

    def test_incompatible_or_unverified_runtime_refuses_before_write(
        self, subtests, *, mocker
    ) -> None:
        for current, captured, message in (
            (
                dict[str, object](pid=111),
                mocker.sentinel.process,
                "incompatible",
            ),
            (
                self.current_runtime(
                    handoff_state="finalized",
                    handoff_transaction_id="txn-previous",
                    serving_payload_sha256=None,
                ),
                mocker.sentinel.process,
                "incompatible",
            ),
            (self.current_runtime(), None, "generation"),
            (self.current_runtime(pid=True), mocker.sentinel.process, "identity"),
        ):
            with subtests.test(message=message):
                payload = FakeTransaction(self.ctx)
                if captured is None:
                    mocker.patch.object(process, "capture_executable", return_value=None)
                with pytest.raises(errors.InstallError, match=message):
                    self.deploy(payload, current, mocker=mocker)
                assert payload.events == []

    def test_unidentified_listener_refuses_fresh_install_before_write(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        mocker.patch.object(process, "listener_pids", return_value=[111])

        with pytest.raises(errors.InstallError, match="identity"):
            self.deploy(payload, None, mocker=mocker)

        assert payload.events == []

    def test_fresh_failure_rolls_back(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("service failed"), mocker=mocker)
        mocker.patch.object(process, "listener_pids", return_value=[])
        with pytest.raises(errors.InstallError, match="service failed"):
            self.deploy(payload, None, adapter=service, mocker=mocker)
        assert payload.events == ["commit", "activate", "rollback"]

    def test_service_admission_rejection_rolls_back_without_native_cleanup(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(
            failure=errors.NativeServiceUnavailableError("systemd user manager is unavailable"),
            mocker=mocker,
        )
        service.uninstall_mock.side_effect = errors.InstallError("user bus is unavailable")
        terminate = mocker.spy(service, "terminate_runtime")
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(errors.NativeServiceUnavailableError, match="systemd user manager"):
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert payload.events == ["commit", "activate", "rollback"]
        service.uninstall_mock.assert_not_called()
        terminate.assert_not_called()

    @pytest.mark.parametrize("stage", ["runtime-cleanup", "payload-rollback"])
    @pytest.mark.parametrize("public", [False, True])
    def test_fresh_failure_retains_initial_and_compensation_causes(
        self, stage: str, public: bool, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        initial = (
            errors.InstallError("scheduled successor is unproved")
            if public
            else OSError("private initial path and token")
        )
        compensation = (
            errors.InstallError("generation removal failed (OS error 32)")
            if public
            else OSError("private compensation path and token")
        )
        service = FakeServiceAdapter(failure=initial, mocker=mocker)
        mocker.patch.object(process, "listener_pids", return_value=[])
        if stage == "runtime-cleanup":
            service.uninstall_mock.side_effect = compensation
        else:
            mocker.patch.object(payload, "rollback", side_effect=compensation)

        with pytest.raises(errors.InstallError) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        message = str(raised.value)
        assert "fresh installation failed" in message
        assert (
            "scheduled successor is unproved" if public else "operating-system operation failed"
        ) in message
        assert (
            "generation removal failed (OS error 32)"
            if public
            else "operating-system operation failed"
        ) in message
        assert "private" not in message
        assert (
            "payload rollback failed" if stage == "payload-rollback" else "cleanup failure"
        ) in message
        if stage == "runtime-cleanup":
            assert isinstance(raised.value, apply.UnknownDeploymentOutcome)
            assert payload.events == ["commit", "activate", ("preserve", message)]
        else:
            assert payload.events == ["commit", "activate"]

    def test_fresh_failure_does_not_claim_a_failed_recovery_record_was_preserved(
        self, *, mocker
    ) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("startup failed"), mocker=mocker)
        service.uninstall_mock.side_effect = errors.InstallError("native cleanup failed")
        mocker.patch.object(
            payload,
            "preserve_for_recovery",
            side_effect=errors.InstallError("journal ownership changed"),
        )
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(apply.UnknownDeploymentOutcome) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        message = str(raised.value)
        assert "fresh installation failed: startup failed" in message
        assert "cleanup failure: native cleanup failed" in message
        assert "recovery record failed: journal ownership changed" in message
        assert "transaction preserved" not in message
        assert payload.events == ["commit", "activate"]

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (OSError(13, "private detail"), "operating-system operation failed (OS error 13)"),
            (OSError("private detail"), "operating-system operation failed"),
            (RuntimeError("private detail"), "operation failed"),
            (KeyboardInterrupt("private detail"), "operation failed"),
        ],
    )
    def test_public_failure_projection_does_not_expose_exception_types(
        self, failure: BaseException, expected: str
    ) -> None:
        assert errors.failure_summary(failure) == expected

    def test_service_admission_failure_retains_failed_payload_rollback(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(
            failure=errors.NativeServiceUnavailableError("user service is unavailable"),
            mocker=mocker,
        )
        mocker.patch.object(
            payload,
            "rollback",
            side_effect=errors.InstallError("generation removal failed (OS error 5)"),
        )
        mocker.patch.object(process, "listener_pids", return_value=[])
        terminate = mocker.spy(service, "terminate_runtime")

        with pytest.raises(errors.NativeServiceUnavailableError) as raised:
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert str(raised.value) == (
            "fresh installation failed: user service is unavailable; "
            "payload rollback failed: generation removal failed (OS error 5)"
        )
        assert payload.events == ["commit", "activate"]
        service.uninstall_mock.assert_not_called()
        terminate.assert_not_called()

    def test_fresh_failure_preserves_unconfirmed_candidate_cleanup(self, *, mocker) -> None:
        payload = FakeTransaction(self.ctx)
        service = FakeServiceAdapter(failure=errors.InstallError("service failed"), mocker=mocker)
        service.uninstall_mock.side_effect = errors.InstallError("cleanup failed")
        mocker.patch.object(process, "listener_pids", return_value=[])

        with pytest.raises(apply.UnknownDeploymentOutcome, match="cleanup is unconfirmed"):
            self.deploy(payload, None, adapter=service, mocker=mocker)

        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                "fresh installation failed: service failed; "
                "candidate runtime cleanup is unconfirmed; cleanup failure: cleanup failed; "
                "transaction preserved for recovery",
            ),
        ]

    def test_upgrade_failure_rolls_back_or_preserves_unknown_outcome(self, *, mocker) -> None:
        current = self.current_runtime()
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])

        rolled_back = FakeTransaction(self.ctx)
        rollback_service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(
            apply, "request_handoff", side_effect=errors.InstallError("handoff failed")
        )
        resume = mocker.patch.object(apply.handoff, "resume_responses")
        with pytest.raises(errors.InstallError, match="handoff failed"):
            self.deploy(rolled_back, current, adapter=rollback_service, mocker=mocker)
        assert rolled_back.events == ["commit", "activate", "rollback"]
        resume.assert_called_once_with(
            self.ctx,
            source_listener=process.OwnedProcess(111, self.ctx.executable, 1.0),
            source_runtime=current,
            runtime_reader=mocker.ANY,
        )
        rollback_service.install_mock.assert_not_called()

        unknown = FakeTransaction(self.ctx)
        preserved_service = FakeServiceAdapter(mocker=mocker)
        resume.reset_mock()
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=apply.UnknownDeploymentOutcome("outcome unknown"),
        )
        with pytest.raises(apply.UnknownDeploymentOutcome, match="outcome unknown"):
            self.deploy(unknown, current, adapter=preserved_service, mocker=mocker)
        assert unknown.events == ["commit", "activate", ("preserve", "outcome unknown")]
        resume.assert_not_called()
        preserved_service.install_mock.assert_not_called()

    def test_upgrade_preserves_a_predecessor_that_cannot_reopen_admission(self, *, mocker) -> None:
        """A restored supervisor is not a successful rollback until admission reopens."""
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime()
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[111])
        mocker.patch.object(
            apply,
            "request_handoff",
            side_effect=errors.InstallError("successor unavailable"),
        )
        mocker.patch.object(
            apply.handoff,
            "resume_responses",
            side_effect=errors.InstallError("drain release is unavailable"),
        )

        with pytest.raises(
            apply.UnknownDeploymentOutcome,
            match="could not restore predecessor admission",
        ) as raised:
            self.deploy(payload, current, adapter=service, mocker=mocker)

        assert str(raised.value) == (
            "native supervisor rollback could not restore predecessor admission; "
            "upgrade failure: successor unavailable; "
            "admission recovery failure: drain release is unavailable"
        )
        assert payload.events == [
            "commit",
            "activate",
            (
                "preserve",
                str(raised.value),
            ),
        ]

    def test_explicit_rollback_reuses_upgrade_and_projects_precise_releases(
        self, *, mocker
    ) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        install = mocker.patch.object(
            apply,
            "install",
            return_value={"state": "upgraded", "runtime": {"pid": 654}},
        )
        service = FakeServiceAdapter(mocker=mocker)

        def runtime_reader(_ctx):
            return {"pid": 321}

        result = apply.rollback(
            self.ctx,
            retained,
            adapter=service,
            runtime_reader=runtime_reader,
            timeout_seconds=12.5,
        )

        assert result == {
            "state": "rolled_back",
            "from_release": "1.2.3",
            "to_release": "1.2.2",
            "runtime": {"pid": 654},
        }
        install.assert_called_once_with(
            self.ctx,
            payload,
            adapter=service,
            runtime_reader=runtime_reader,
            timeout_seconds=12.5,
        )

    def test_explicit_rollback_refuses_before_any_change_when_no_runtime_answers(
        self, *, mocker
    ) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        begin = mocker.patch.object(transaction, "begin_rollback_transaction")
        install = mocker.patch.object(apply, "install")
        service = FakeServiceAdapter(mocker=mocker)

        with pytest.raises(errors.InstallError, match="verified running successor"):
            apply.rollback(
                self.ctx,
                retained,
                adapter=service,
                runtime_reader=lambda _ctx: None,
            )

        begin.assert_not_called()
        install.assert_not_called()
        service.install_mock.assert_not_called()
        service.uninstall_mock.assert_not_called()

    def test_explicit_rollback_uses_current_runtime_handoff_capability(self, *, mocker) -> None:
        """Rollback keeps admission open when the current listener supports handoff."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(release="1.2.3")
        runtime = self.successor(release="1.2.2")
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        mocker.patch.object(process, "capture_executable", return_value=source)
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        replace = mocker.patch.object(
            apply,
            "_replace_native_generation",
            return_value=runtime,
        )
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)
        control = generation.context(self.ctx, "c" * 32)
        mocker.patch.object(generation, "control_context", return_value=control)

        result = apply.rollback(
            self.ctx,
            retained,
            adapter=service,
            runtime_reader=lambda _ctx: current,
        )

        assert result == {
            "state": "rolled_back",
            "from_release": "1.2.3",
            "to_release": "1.2.2",
            "runtime": runtime,
        }
        drain.assert_not_called()
        replace.assert_not_called()
        request.assert_called_once()
        service.install_mock.assert_called_once_with(control)

    def test_explicit_rollback_replaces_a_runtime_without_handoff_capability(
        self, *, mocker
    ) -> None:
        """A reverse transition keeps the newest control supervisor authoritative."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(
            release="1.2.3",
            handoff_capabilities=["repeatable"],
        )
        runtime = self.successor(release="1.2.2")
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        mocker.patch.object(process, "capture_executable", return_value=source)
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)
        request = mocker.patch.object(apply, "request_handoff")
        control = generation.context(self.ctx, "c" * 32)
        mocker.patch.object(generation, "control_context", return_value=control)

        result = apply.rollback(
            self.ctx,
            retained,
            adapter=service,
            runtime_reader=lambda _ctx: current,
        )

        assert result == {
            "state": "rolled_back",
            "from_release": "1.2.3",
            "to_release": "1.2.2",
            "runtime": runtime,
        }
        drain.assert_called_once()
        terminate.assert_called_once_with(source, timeout_seconds=30)
        request.assert_not_called()
        service.install_mock.assert_called_once_with(control)

    def test_explicit_rollback_requires_an_upgrade_result(self, *, mocker) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        mocker.patch.object(
            transaction,
            "begin_rollback_transaction",
            return_value=FakeTransaction(self.ctx),
        )
        mocker.patch.object(
            apply,
            "install",
            return_value={"state": "installed", "runtime": {"pid": 654}},
        )

        with pytest.raises(errors.InstallError, match="verified running successor"):
            apply.rollback(
                self.ctx,
                retained,
                adapter=FakeServiceAdapter(mocker=mocker),
                runtime_reader=lambda _ctx: {"pid": 321},
            )

    def test_request_handoff_resolves_finalized_rolled_back_and_unknown(
        self, subtests, *, mocker
    ) -> None:
        current = self.current_runtime()
        expected = FakeTransaction(self.ctx).expected
        successor = self.successor()
        source_listener = process.OwnedProcess(111, self.ctx.executable, 1.0)
        for response, resolution, expected_result in (
            ({"runtime": successor}, None, successor),
            ({"runtime": None}, ("finalized", successor), successor),
        ):
            with subtests.test(resolution=resolution):
                mocker.patch.object(apply.handoff, "request", return_value=response)
                mocker.patch.object(
                    apply.handoff,
                    "resolve_after_controller_failure",
                    return_value=resolution,
                )
                assert (
                    apply.request_handoff(
                        self.ctx,
                        expected,
                        current=current,
                        source_listener=source_listener,
                        runtime_reader=lambda _ctx: current,
                        timeout_seconds=1,
                    )
                    == expected_result
                )

        original = errors.InstallError("handoff failed")
        mocker.patch.object(apply.handoff, "request", side_effect=original)
        mocker.patch.object(
            apply.handoff,
            "resolve_after_controller_failure",
            return_value=("rolled_back", current),
        )
        with pytest.raises(errors.InstallError, match="handoff failed"):
            apply.request_handoff(
                self.ctx,
                expected,
                current=current,
                source_listener=source_listener,
                runtime_reader=lambda _ctx: current,
                timeout_seconds=1,
            )

        mocker.patch.object(
            apply.handoff,
            "resolve_after_controller_failure",
            return_value=("unknown", None),
        )
        with pytest.raises(apply.UnknownDeploymentOutcome):
            apply.request_handoff(
                self.ctx,
                expected,
                current=current,
                source_listener=source_listener,
                runtime_reader=lambda _ctx: current,
                timeout_seconds=1,
            )

    def test_wait_for_serving_runtime_requires_exact_identity(self, *, mocker) -> None:
        expected = FakeTransaction(self.ctx).expected
        match = self.successor()
        snapshots = [None, {**match, "pid": True}, {**match, "release": "wrong"}, match]
        mocker.patch.object(process, "verified_proxy_listener_pids", side_effect=[[], [222]])
        mocker.patch.object(apply.time, "monotonic", side_effect=map(float, range(6)))
        mocker.patch.object(apply.time, "sleep")
        assert (
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: snapshots.pop(0),
                timeout_seconds=10,
            )
            == match
        )

        mocker.patch.object(apply.time, "monotonic", side_effect=[0.0, 0.1, 0.2])
        with pytest.raises(errors.InstallError, match="SERVING identity"):
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: None,
                timeout_seconds=0.15,
            )

    def test_wait_for_serving_runtime_accepts_exact_process_when_tcp_inventory_lags(
        self, *, mocker
    ) -> None:
        """Loopback identity and exact process ownership outlive stale TCP inventory."""
        expected = FakeTransaction(self.ctx).expected
        match = self.successor()
        owned = process.OwnedProcess(222, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[])
        mocker.patch.object(process, "capture_executable", return_value=owned)

        assert (
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: match,
                timeout_seconds=0.2,
            )
            == match
        )

    def test_read_runtime_accepts_only_http_200_json_objects(self, *, mocker) -> None:
        class Response:
            def __init__(self, status: int, payload: bytes) -> None:
                self.status, self.payload = status, payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        open_request = mocker.patch.object(
            control.loopback,
            "open_request",
            return_value=Response(200, b'{"pid": 222}'),
        )
        assert control.read_runtime(self.ctx) == {"pid": 222}
        for response in (
            Response(204, b""),
            Response(200, b"[]"),
            Response(200, b"bad"),
        ):
            open_request.return_value = response
            assert control.read_runtime(self.ctx) is None
        open_request.side_effect = urllib.error.URLError("offline")
        assert control.read_runtime(self.ctx) is None


def test_install_has_no_json_publication_proof_loader() -> None:
    assert not hasattr(install, "publication_proof_from_file")


@pytest.mark.parametrize(
    "evidence",
    [
        "active",
        "degraded",
        "runtime_absent",
        "different_artifact",
        "different_payload",
        "supervisor_absent",
        "supervisor_replaced",
        "pending_transaction",
    ],
)
def test_install_of_exact_active_artifact_preserves_every_projection(
    tmp_path: Path, evidence: str, *, mocker
):
    ctx = install_context(tmp_path)
    installed = install_payload(ctx, mocker=mocker)
    released = released_artifact(
        executable_content=(
            b"different-native-executable"
            if evidence == "different_artifact"
            else b"native-executable-fixture"
        )
    )
    mocker.patch.object(install.artifact, "admit", return_value=released)
    native = mocker.Mock()
    native.configured_executable.return_value = {
        "supervisor_absent": None,
        "supervisor_replaced": str(tmp_path / "unrelated-executable"),
    }.get(evidence, ctx.executable)
    mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.native_service.adapter", return_value=native
    )
    mocker.patch.object(
        control,
        "status",
        return_value={
            "state": "degraded" if evidence == "degraded" else "running",
            "release": released.version,
            "runtime": (
                None
                if evidence == "runtime_absent"
                else {
                    "release_receipt_sha256": installed.receipt_sha256,
                    "serving_payload_sha256": (
                        "0" * 64
                        if evidence == "different_payload"
                        else installed.expected["serving_payload_sha256"]
                    ),
                }
            ),
        },
    )
    pending = evidence == "pending_transaction"
    if pending:
        mocker.patch.object(payload_state, "status", return_value={"state": "prepared"})
    begin = mocker.spy(transaction, "begin_transaction")
    applied = mocker.patch.object(apply, "install")
    projections = (
        payload_state.installed_path(ctx),
        generation.selector_path(ctx),
        Path(ctx.command),
    )
    before = {path: path.read_bytes() for path in projections}

    if evidence == "active":
        assert install.install_asset(
            ctx, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
        ) == {"state": "unchanged", "release": installed.release}
    else:
        error = errors.RecoveryRequiredError if pending else errors.InstallError
        message = "complete payload recovery" if pending else "not the verified active installation"
        with pytest.raises(error, match=message):
            install.install_asset(
                ctx, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
            )

    assert {path: path.read_bytes() for path in projections} == before
    assert not payload_state.journal_path(ctx).exists()
    begin.assert_not_called()
    applied.assert_not_called()
    native.install.assert_not_called()
    native.uninstall.assert_not_called()


def _unsupervised_installation(root: Path, *, service: str, serving: bool = False, mocker):
    """Install one verified payload whose native supervision is absent or stopped."""
    ctx = install_context(root)
    install_payload(ctx, mocker=mocker)
    released = released_artifact()
    mocker.patch.object(install.artifact, "admit", return_value=released)
    control_ctx = generation.control_context(ctx)
    committed = identity.committed_payload(Path(ctx.executable))
    assert committed is not None
    bound = {"control": service == "installed"}
    native = mocker.Mock()
    native.status.return_value = service
    native.configured_executable.side_effect = lambda _ctx: (
        control_ctx.executable if bound["control"] else None
    )
    native.install.side_effect = lambda _ctx: bound.update(control=True)
    mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.native_service.adapter", return_value=native
    )
    mocker.patch.object(control, "adapter", return_value=native)
    runtime = {
        "pid": 4242,
        **committed.handoff(),
        "payload_manifest_sha256": committed.manifest_sha256,
        "accepting": True,
        "draining": False,
    }
    listeners = [4242] if serving else []
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=listeners)
    mocker.patch.object(control.process, "listener_pids", return_value=listeners)
    mocker.patch.object(control, "read_runtime", return_value=runtime if serving else None)
    return ctx, control_ctx, native, committed, runtime


@pytest.mark.parametrize(
    ("service", "serving", "command_removed"),
    [
        ("absent", False, True),
        ("absent", False, False),
        ("installed", False, False),
        ("absent", True, False),
        ("installed", True, False),
    ],
)
def test_install_of_the_exact_artifact_restores_an_unsupervised_verified_installation(
    tmp_path: Path, service: str, serving: bool, command_removed: bool, *, mocker
) -> None:
    ctx, control_ctx, native, committed, runtime = _unsupervised_installation(
        tmp_path, service=service, serving=serving, mocker=mocker
    )
    if command_removed:
        Path(ctx.command).unlink()
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime", return_value=runtime)
    begin = mocker.spy(transaction, "begin_transaction")
    applied = mocker.patch.object(apply, "install")
    installed_bytes = payload_state.installed_path(ctx).read_bytes()
    selector_bytes = generation.selector_path(ctx).read_bytes()

    assert install.install_asset(
        ctx,
        tmp_path / "release.tar.gz",
        trust_anchor=tmp_path / "allowed-signers",
        timeout_seconds=12.5,
    ) == {"state": "installed", "runtime": runtime}

    assert command.status(Path(ctx.command), Path(control_ctx.executable))["state"] == "owned"
    native.install.assert_called_once_with(control_ctx)
    wait.assert_called_once_with(
        ctx,
        committed.handoff(),
        runtime_reader=control.read_runtime,
        timeout_seconds=12.5,
    )
    assert payload_state.installed_path(ctx).read_bytes() == installed_bytes
    assert generation.selector_path(ctx).read_bytes() == selector_bytes
    assert not payload_state.journal_path(ctx).exists()
    begin.assert_not_called()
    applied.assert_not_called()
    native.uninstall.assert_not_called()


@pytest.mark.parametrize(
    "evidence",
    [
        "different_artifact",
        "foreign_listener",
        "service_unknown",
        "service_running",
        "other_port",
        "foreign_command",
        "changed_payload",
    ],
)
def test_install_of_the_exact_artifact_refuses_an_unproved_unsupervised_installation(
    tmp_path: Path, evidence: str, *, mocker
) -> None:
    ctx, _control_ctx, native, _committed, _runtime = _unsupervised_installation(
        tmp_path,
        service={"service_unknown": "unknown", "service_running": "running"}.get(
            evidence, "absent"
        ),
        mocker=mocker,
    )
    if evidence == "different_artifact":
        mocker.patch.object(
            install.artifact,
            "admit",
            return_value=released_artifact(executable_content=b"different-native-executable"),
        )
    if evidence == "foreign_listener":
        mocker.patch.object(control.process, "listener_pids", return_value=[99])
    if evidence == "foreign_command":
        Path(ctx.command).unlink()
        Path(ctx.command).write_text("another owner\n", encoding="utf-8")
    if evidence == "changed_payload":
        Path(ctx.executable).write_bytes(b"changed-native-executable")
    requested = replace(ctx, port=ctx.port + 1) if evidence == "other_port" else ctx
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime")
    begin = mocker.spy(transaction, "begin_transaction")
    command_bytes = Path(ctx.command).read_bytes()
    message = (
        "selected control-plane generation identity is invalid"
        if evidence == "changed_payload"
        else "not the verified active installation"
    )

    with pytest.raises(errors.InstallError, match=message):
        install.install_asset(
            requested, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
        )

    assert Path(ctx.command).read_bytes() == command_bytes
    assert not payload_state.journal_path(ctx).exists()
    native.install.assert_not_called()
    native.uninstall.assert_not_called()
    wait.assert_not_called()
    begin.assert_not_called()


def test_install_of_the_exact_artifact_keeps_restored_supervision_when_no_listener_is_proved(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native, _committed, _runtime = _unsupervised_installation(
        tmp_path, service="absent", mocker=mocker
    )
    Path(ctx.command).unlink()
    mocker.patch.object(
        control.apply,
        "wait_for_serving_runtime",
        side_effect=errors.InstallError("released successor did not prove SERVING identity"),
    )

    with pytest.raises(
        errors.InstallError,
        match=r"native supervisor started but no listener proved SERVING identity within 3s",
    ):
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
            timeout_seconds=3.0,
        )

    assert command.status(Path(ctx.command), Path(control_ctx.executable))["state"] == "owned"
    native.install.assert_called_once_with(control_ctx)
    native.uninstall.assert_not_called()
    assert not payload_state.journal_path(ctx).exists()


def test_install_admission_failure_closes_the_prepared_transaction(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    payload = mocker.Mock()
    payload.rollback_if_prepared.return_value = True
    mocker.patch.object(install.artifact, "admit", return_value="released")
    mocker.patch.object(install.transaction, "begin_transaction", return_value=payload)
    mocker.patch.object(
        install.apply,
        "install",
        side_effect=errors.InstallError("installed runtime identity is not verified"),
    )

    with pytest.raises(errors.InstallError, match="identity is not verified"):
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
        )

    payload.rollback_if_prepared.assert_called_once_with()


@pytest.mark.parametrize("unavailable", [False, True])
def test_install_admission_failure_retains_failed_prepared_cleanup(
    tmp_path: Path, unavailable: bool, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    payload = mocker.Mock()
    failure_type = errors.NativeServiceUnavailableError if unavailable else errors.InstallError
    initial = failure_type("initial installation boundary failed")
    payload.rollback_if_prepared.side_effect = errors.InstallError(
        "payload generation removal failed (OS error 32)"
    )
    mocker.patch.object(install.artifact, "admit", return_value="released")
    mocker.patch.object(install.transaction, "begin_transaction", return_value=payload)
    mocker.patch.object(install.apply, "install", side_effect=initial)

    with pytest.raises(failure_type) as raised:
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
        )

    assert str(raised.value) == (
        "installation failed: initial installation boundary failed; "
        "prepared payload cleanup failed: payload generation removal failed (OS error 32)"
    )
    assert raised.value.code == initial.code
    assert raised.value.next_command == initial.next_command
    payload.rollback_if_prepared.assert_called_once_with()
