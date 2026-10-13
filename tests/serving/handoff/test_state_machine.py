"""Parent rolling-handoff state machine contracts."""

from __future__ import annotations

import struct
import threading
from pathlib import Path

import pytest

from openai_responses_proxy.serving.handoff import protocol as handoff_protocol_module
from tests.serving.handoff.fixtures import HandoffFixture
from tests.serving.handoff.fixtures import child_message
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_child
from tests.serving.handoff.fixtures import fake_server
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import handoff_outcome_ready
from tests.serving.handoff.fixtures import matching_health
from tests.serving.handoff.fixtures import wait_until

ROOT = Path(__file__).resolve().parents[3]


class TestParentHandoffStateMachine(HandoffFixture):
    """Legal/illegal runtime behavior of the prepare -> commit -> finalize driver."""

    def test_parent_unit_context_owns_its_log_sink(self):
        assert self.context.log is not entrypoint_module.operational_log.log
        self.context.log("fixture-event")
        assert self.log_events == ["fixture-event"]

    def test_handoff_context_cannot_mutate_request_admission(self):
        assert not hasattr(self.context, "set_draining")

    def committed(self, *after_ready, health=None, mocker, **kwargs):
        expected = kwargs.pop("expected", expected_metadata())
        child = kwargs.pop("child", fake_child(mocker=mocker))
        server = kwargs.pop("server", fake_server(mocker=mocker))
        child.recv_message.side_effect = [
            child_message("ready", child, expected),
            *after_ready,
        ]
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(server, expected, self.context, **kwargs)
        del health
        outcome = self.p.commit(server, prepared, self.context)
        return outcome, server, child, expected

    def test_prepare_binds_protocol_identity_to_runtime_pid_not_launcher_pid(self, *, mocker):
        expected = expected_metadata()
        child = fake_child(pid=4242, mocker=mocker)
        child.runtime_pid = 5150
        child.recv_message.return_value = {
            "type": "ready",
            "pid": child.runtime_pid,
            "transaction_id": expected["transaction_id"],
            "protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
            "release": expected["release"],
            "serving_payload_sha256": expected["serving_payload_sha256"],
            "release_receipt_sha256": expected["release_receipt_sha256"],
            "manifest_sha256": expected["manifest_sha256"],
        }
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(
            fake_server(mocker=mocker), expected, self.context, timeout_seconds=1
        )

        assert prepared["child"] is child
        assert self.p._HANDOFF_SESSION["child_pid"] == child.runtime_pid

    def test_prepare_then_commit_finalizes_and_never_reopens_admission_itself(self, *, mocker):
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        outcome, server, _, _ = self.committed(
            child_message("serving", child, expected),
            child_message("finalized", child, expected),
            child=child,
            expected=expected,
            timeout_seconds=5,
            mocker=mocker,
        )
        assert outcome == "finalized"
        server.shutdown.assert_called_once()
        child.send_message.assert_any_call({"type": "commit"})
        child.send_message.assert_any_call({"type": "finalize"})
        assert self.p._HANDOFF_SESSION.get("outcome") == "finalized"
        assert handoff_outcome_ready().is_set()

    def test_parent_stops_accepting_before_child_serves_without_closing_admission(self, *, mocker):
        server = fake_server(mocker=mocker)
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        order = []
        server.shutdown.side_effect = lambda: order.append("shutdown")

        def send_message(message):
            order.append(f"send:{message.get('type')}")

        child.send_message.side_effect = send_message
        child.recv_message.side_effect = [
            child_message("ready", child, expected),
            child_message("serving", child, expected),
            child_message("finalized", child, expected),
        ]
        observing_context = entrypoint_module._handoff_context()
        object.__setattr__(
            observing_context,
            "successor_executable",
            lambda: observing_context.executable,
        )
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(server, expected, observing_context, timeout_seconds=5)
        outcome = self.p.commit(server, prepared, observing_context)

        assert outcome == "finalized"
        assert order.index("shutdown") < order.index("send:commit")
        assert child.recv_message.call_args_list[1].args == (5,)

    def test_simultaneous_handoff_is_rejected(self, *, mocker):
        server = fake_server(mocker=mocker)
        expected = expected_metadata()
        release_spawn = threading.Event()
        child = fake_child(mocker=mocker)
        child.recv_message.side_effect = [child_message("ready", child, expected)]

        def slow_spawn(*_args, **_kwargs):
            release_spawn.wait(timeout=5)
            return child

        errors = []

        def second_attempt():
            try:
                self.p.prepare(server, expected, self.context, timeout_seconds=5)
            except self.p.HandoffError as exc:
                errors.append(exc)

        mocker.patch.object(self.p, "spawn_child", side_effect=slow_spawn)
        first = threading.Thread(
            target=self.p.prepare,
            args=(server, expected, self.context),
            kwargs={"timeout_seconds": 5},
        )
        first.start()
        assert wait_until(lambda: self.p._HANDOFF_SESSION.get("state") == "preparing", timeout=2)
        second = threading.Thread(target=second_attempt)
        second.start()
        second.join(timeout=5)
        release_spawn.set()
        first.join(timeout=5)
        assert len(errors) == 1
        assert "already in progress" in str(errors[0]).lower()

    def test_prepare_rejects_incomplete_identity_and_recycles_finalized_session(
        self, subtests, *, mocker
    ):
        server = fake_server(mocker=mocker)
        for expected in ({}, {**expected_metadata(), "release": ""}):
            with (
                subtests.test(expected=expected),
                pytest.raises(self.p.HandoffError, match="identity is incomplete"),
            ):
                self.p.prepare(server, expected, self.context)

        child = fake_child(mocker=mocker)
        expected = expected_metadata(transaction_id="txn-recycled")
        child.recv_message.return_value = child_message("ready", child, expected)
        self.p._HANDOFF_SESSION["state"] = "finalized"
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(server, expected, self.context)
        assert prepared["expected"] == expected
        assert self.p._HANDOFF_SESSION["state"] == "ready"

    def test_transition_and_disk_identity_helpers_fail_closed(self, *, mocker):
        self.p._HANDOFF_SESSION["state"] = "idle"
        with pytest.raises(self.p.HandoffError, match="illegal handoff transition"):
            self.p._transition("serving")

        expected = {
            "release": entrypoint_module.release_version(),
            "serving_payload_sha256": entrypoint_module.loaded_serving_payload_sha256(),
            "release_receipt_sha256": entrypoint_module.release_receipt_sha256(),
            "manifest_sha256": self.context.payload_manifest_sha256(),
        }
        committed = mocker.Mock(handoff=lambda: expected)
        object.__setattr__(self.context, "committed_payload", lambda: committed)
        assert self.p.disk_payload_matches_expected(expected, self.context)
        assert not self.p.disk_payload_matches_expected(
            {**expected, "release": "wrong"}, self.context
        )
        object.__setattr__(self.context, "committed_payload", lambda: None)
        assert not self.p.disk_payload_matches_expected(expected, self.context)
        missing_context = entrypoint_module._handoff_context()
        object.__setattr__(missing_context, "executable", Path("/missing/proxy"))
        object.__setattr__(missing_context, "payload_manifest_sha256", lambda: None)
        assert missing_context.payload_manifest_sha256() is None

        self.p._HANDOFF_SESSION.update(state="ready", transaction_id="txn-identity")
        identity = self.p.runtime_identity(self.context)
        assert identity["accepting"]
        assert identity["handoff_transaction_id"] == "txn-identity"

    def test_probe_health_rejects_oversized_invalid_and_non_object_payloads(
        self, subtests, *, mocker
    ):
        response = mocker.MagicMock()
        response.__enter__.return_value = response
        mocker.patch.object(
            handoff_protocol_module.loopback,
            "open_request",
            return_value=response,
        )
        for payload, error in (
            (
                b"x" * (handoff_protocol_module.HANDOFF_CONTROL_MAX_BYTES + 1),
                "exceeds the control limit",
            ),
            (b"{", "response is invalid"),
            (b"[]", "must be an object"),
        ):
            with subtests.test(error=error):
                response.read.return_value = payload
                with pytest.raises(self.p.HandoffError, match=error):
                    self.p.probe_health(8791, timeout_seconds=1)
        response.read.return_value = b'{"ok":true}'
        assert self.p.probe_health(8791, timeout_seconds=1) == {"ok": True}

    def test_probe_health_retries_until_the_successor_serves_the_expected_identity(self, *, mocker):
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        old = {**matching_health(child, expected), "pid": child.runtime_pid - 1}
        current = matching_health(child, expected)
        mocker.patch.object(
            handoff_protocol_module,
            "_read_health",
            side_effect=(old, current),
        )
        mocker.patch.object(
            handoff_protocol_module.time,
            "monotonic",
            side_effect=(0.0, 0.1, 0.2),
        )
        mocker.patch.object(handoff_protocol_module.time, "sleep")

        assert (
            self.p.probe_health(
                8791,
                timeout_seconds=1,
                expected={
                    "pid": child.runtime_pid,
                    "release": expected["release"],
                },
            )
            == current
        )

    def test_probe_health_retries_failed_observations_until_exact_identity(self, *, mocker):
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        current = matching_health(child, expected)
        mocker.patch.object(
            handoff_protocol_module,
            "_read_health",
            side_effect=(
                struct.error("transient observer failure"),
                current,
            ),
        )
        mocker.patch.object(
            handoff_protocol_module.time,
            "monotonic",
            side_effect=(0.0, 0.1, 0.2),
        )
        mocker.patch.object(handoff_protocol_module.time, "sleep")

        assert (
            self.p.probe_health(
                8791,
                timeout_seconds=1,
                expected={
                    "pid": child.runtime_pid,
                    "release": expected["release"],
                },
            )
            == current
        )

    def test_prepare_failures_never_cross_admission_and_reset_when_child_exit_is_confirmed(
        self, subtests, *, mocker
    ):
        expected = expected_metadata()
        for name, spawn_result in (
            ("spawn", OSError("fork failed")),
            ("ready timeout", TimeoutError("no ready message")),
        ):
            with subtests.test(case=name):
                handoff_module.reset_session_to_idle()
                server = fake_server(mocker=mocker)
                child = fake_child(mocker=mocker)
                child.recv_message.side_effect = spawn_result
                options = (
                    {"side_effect": spawn_result} if name == "spawn" else {"return_value": child}
                )
                mocker.patch.object(self.p, "spawn_child", **options)
                with pytest.raises(self.p.HandoffError):
                    self.p.prepare(server, expected, self.context, timeout_seconds=1)
                server.shutdown.assert_not_called()
                assert self.p._HANDOFF_SESSION.get("state") == "idle"
                assert child.terminate_bounded.call_count == int(name != "spawn")

    def test_commit_failures_roll_back_after_the_accept_barrier(self, subtests, *, mocker):
        child = fake_child(mocker=mocker)
        cases = (
            ("commit pipe", (), BrokenPipeError("commit pipe failed")),
            ("serving timeout", (TimeoutError("no serving message"),), None),
            (
                "serving pid",
                ({"type": "serving", "pid": 1, "transaction_id": "txn-1"},),
                None,
            ),
            (
                "serving transaction",
                (
                    {
                        "type": "serving",
                        "pid": child.process.pid,
                        "transaction_id": "wrong-txn",
                    },
                ),
                None,
            ),
        )
        for name, messages, send_failure in cases:
            with subtests.test(case=name):
                handoff_module.reset_session_to_idle()
                child = fake_child(mocker=mocker)
                child.send_message.side_effect = send_failure
                outcome, server, child, _ = self.committed(
                    *messages, child=child, timeout_seconds=1, mocker=mocker
                )
                assert outcome == "rolled_back"
                server.shutdown.assert_called_once()
                child.terminate_bounded.assert_called_once()
                assert self.p._HANDOFF_SESSION.get("outcome") == "rolled_back"
                assert handoff_outcome_ready().is_set()

    def test_finalize_failure_logs_only_the_failed_phase_and_exception_class(self, *, mocker):
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        child.recv_message.side_effect = [
            child_message("ready", child, expected),
            child_message("serving", child, expected),
            OSError("Authorization=Bearer do-not-log"),
        ]
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        server = fake_server(mocker=mocker)
        prepared = self.p.prepare(server, expected, self.context, timeout_seconds=1)
        log = mocker.Mock()
        context = entrypoint_module._handoff_context()
        object.__setattr__(context, "log", log)

        assert self.p.commit(server, prepared, context) == "rolled_back"

        log.assert_called_once_with("event=handoff_commit_failed phase=finalize exception=OSError")
        assert "do-not-log" not in log.call_args.args[0]

    def test_identity_matrices_reject_ready_serving_and_finalized_mismatches(
        self, subtests, *, mocker
    ):
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        fields = {
            "ready": {
                "protocol_version": 1,
                "pid": 999999,
                "transaction_id": "wrong-txn",
                "release": "1.0.24",
                "serving_payload_sha256": "c" * 64,
                "release_receipt_sha256": "e" * 64,
                "manifest_sha256": "d" * 64,
            },
            "serving": {"pid": 1, "transaction_id": "wrong-txn"},
            "finalized": {"pid": 1, "transaction_id": "wrong-txn"},
        }
        for stage, overrides in fields.items():
            for field, bad_value in overrides.items():
                with subtests.test(stage=stage, field=field):
                    handoff_module.reset_session_to_idle()
                    child = fake_child(mocker=mocker)
                    message = {
                        **child_message(stage, child, expected),
                        field: bad_value,
                    }
                    if stage == "ready":
                        child.recv_message.side_effect = [message]
                        mocker.patch.object(self.p, "spawn_child", return_value=child)
                        with pytest.raises(self.p.HandoffError):
                            self.p.prepare(
                                fake_server(mocker=mocker),
                                expected,
                                self.context,
                                timeout_seconds=1,
                            )
                        child.terminate_bounded.assert_called_once()
                        continue
                    after_ready = [child_message("serving", child, expected)]
                    after_ready = [message] if stage == "serving" else after_ready
                    after_ready += [message] * (stage == "finalized")
                    outcome, _, child, _ = self.committed(
                        *after_ready,
                        child=child,
                        timeout_seconds=1,
                        mocker=mocker,
                    )
                    assert outcome == "rolled_back"
                    child.terminate_bounded.assert_called_once()

    def test_health_identity_allows_observability_fields(self, *, mocker):
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        health = {
            **matching_health(child, expected),
            "uptime_seconds": 12.5,
            "active_responses": 0,
            "active_handlers": 1,
            "counters": {},
            "upstream_classifications": {},
            "last_failure": None,
        }
        outcome, _, child, _ = self.committed(
            child_message("serving", child, expected),
            child_message("finalized", child, expected),
            child=child,
            expected=expected,
            health=health,
            timeout_seconds=1,
            mocker=mocker,
        )
        assert outcome == "finalized"
        child.terminate_bounded.assert_not_called()

    def test_abort_falls_back_to_kill_when_terminate_does_not_exit_child_in_time(self, *, mocker):
        server = fake_server(mocker=mocker)
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        child.recv_message.side_effect = TimeoutError("no ready message")
        child.terminate_bounded.return_value = False  # terminate alone was insufficient
        child.kill_bounded = mocker.Mock(return_value=True)
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        with pytest.raises(self.p.HandoffError):
            self.p.prepare(server, expected, self.context, timeout_seconds=1)
        child.terminate_bounded.assert_called_once()
        child.kill_bounded.assert_called_once()

    def test_unconfirmed_abort_never_reports_a_resumable_rollback(self, *, mocker):
        server = fake_server(mocker=mocker)
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        child.recv_message.side_effect = [
            child_message("ready", child, expected),
            BrokenPipeError("commit pipe failed"),
        ]
        child.terminate_bounded.return_value = False
        child.kill_bounded = mocker.Mock(return_value=False)
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(server, expected, self.context, timeout_seconds=1)
        outcome = self.p.commit(server, prepared, self.context)
        assert outcome == "abort_unconfirmed"
        assert self.p._HANDOFF_SESSION["outcome"] == "abort_unconfirmed"
        assert self.p._HANDOFF_SESSION["state"] != "rolled_back"
        server.shutdown.assert_called()

    def test_abort_swallows_control_and_shutdown_errors_but_requires_confirmed_exit(
        self, *, mocker
    ):
        child = fake_child(mocker=mocker)
        child.send_message.side_effect = BrokenPipeError
        child.terminate_bounded.side_effect = OSError
        child.kill_bounded.return_value = True
        self.p._HANDOFF_SESSION["state"] = "serving"
        self.p.abort(child)
        assert self.p._HANDOFF_SESSION["state"] == "rolled_back"

        child = fake_child(mocker=mocker)
        child.terminate_bounded.side_effect = OSError
        child.kill_bounded.side_effect = OSError
        self.p._HANDOFF_SESSION["state"] = "serving"
        with pytest.raises(self.p.HandoffError, match="could not be confirmed exited"):
            self.p.abort(child)

    def test_unconfirmed_precommit_abort_stays_fail_closed_instead_of_returning_idle(
        self, *, mocker
    ):
        server = fake_server(mocker=mocker)
        child = fake_child(mocker=mocker)
        expected = expected_metadata()
        child.recv_message.side_effect = TimeoutError("no ready message")
        child.terminate_bounded.return_value = False
        child.kill_bounded = mocker.Mock(return_value=False)
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        with pytest.raises(self.p.HandoffError):
            self.p.prepare(server, expected, self.context, timeout_seconds=1)
        assert self.p._HANDOFF_SESSION["state"] == "aborting"

    def test_internal_fail_closed_branches_are_bounded(self, *, mocker):
        expected = expected_metadata()
        server = fake_server(mocker=mocker)
        child = fake_child(mocker=mocker)
        child.send_message.side_effect = BrokenPipeError
        child.terminate_bounded.return_value = True
        server.socket.fileno.return_value = 37
        mocker.patch.object(handoff_protocol_module.subprocess, "Popen", return_value=mocker.Mock())
        mocker.patch.object(handoff_protocol_module, "HandoffChild", return_value=child)
        with pytest.raises(BrokenPipeError):
            self.p.spawn_child(server.socket, expected, self.context, is_windows=False)
        child.kill_bounded.assert_not_called()

        self.p._HANDOFF_SESSION["state"] = "idle"
        self.p.abort(fake_child(mocker=mocker))
        assert self.p._HANDOFF_SESSION["state"] == "idle"

        self.p._HANDOFF_SESSION["outcome_ready"] = object()
        self.p._set_outcome("rolled_back")
        assert self.p._HANDOFF_SESSION["outcome"] == "rolled_back"

        invalid_context = entrypoint_module._handoff_context()
        assert not self.p._valid_prepare([], invalid_context)

        child = fake_child(mocker=mocker)
        child.recv_message.return_value = child_message("ready", child, expected)
        mocker.patch.object(self.p, "spawn_child", return_value=child)
        prepared = self.p.prepare(server, expected, self.context, timeout_seconds=1)
        child.send_message.side_effect = BrokenPipeError
        child.terminate_bounded.return_value = False
        child.kill_bounded.return_value = False
        server.shutdown.side_effect = OSError
        outcome = self.p.commit(server, prepared, self.context)
        assert outcome == "abort_unconfirmed"
