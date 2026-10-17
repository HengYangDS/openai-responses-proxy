"""Ready control and finalized successor convergence."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.deployment.handoff.fixtures import admit_handoff_generations
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health
from tests.serving.handoff.fixtures import ready_ack


class TestControllerHandoffWiring(HandoffFixture):
    def test_request_handoff_waits_for_predecessor_exit_and_finalized_successor(
        self, subtests, *, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        for name, alive_states in (
            ("direct", [True, False, True]),
            ("delayed predecessor exit", [True, True, False, True]),
        ):
            with subtests.test(case=name):
                predecessor = process.OwnedProcess(999, ctx.executable, 0.5)
                successor = process.OwnedProcess(1000, ctx.executable, 1.0)
                mocker.patch.object(process, "wait_for_executable", return_value=successor)
                alive_by_pid = {
                    predecessor.pid: iter(alive_states),
                    successor.pid: iter((True, True)),
                }
                mocker.patch.object(
                    process,
                    "owned_process_alive",
                    side_effect=lambda generation, states=alive_by_pid: next(
                        states[generation.pid]
                    ),
                )
                tcp_attribution = mocker.patch.object(process, "listener_pids", return_value=[999])
                mocker.patch.object(handoff, "post_ready", return_value=ready_ack(expected))
                mocker.patch.object(handoff.time, "sleep")
                source_snapshot = idle_runtime()
                finalized_snapshot = matching_health(1000, expected, handoff_state="finalized")
                snapshots = iter((source_snapshot, finalized_snapshot))
                result = handoff.request(
                    ctx,
                    expected,
                    source_listener=predecessor,
                    source_runtime=source_snapshot,
                    runtime_reader=mocker.Mock(
                        side_effect=lambda _ctx, values=snapshots, final=finalized_snapshot: next(
                            values, final
                        )
                    ),
                    timeout_seconds=5,
                )
                assert (result["old_pid"], result["child_pid"]) == (999, 1000)
                tcp_attribution.assert_not_called()

    def test_request_handoff_waits_for_finalized_successor_identity(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor, _, _ = admit_handoff_generations(mocker, ctx)
        serving = matching_health(1000, expected)
        finalized = matching_health(1000, expected, handoff_state="finalized")
        mocker.patch.object(handoff, "post_ready", return_value=ready_ack(expected))
        runtime_reader = mocker.Mock(side_effect=[idle_runtime(), serving, finalized])
        mocker.patch.object(handoff.time, "sleep")

        result = handoff.request(
            ctx,
            expected,
            source_listener=predecessor,
            source_runtime=idle_runtime(),
            runtime_reader=runtime_reader,
            timeout_seconds=5,
        )

        assert result["runtime"] == finalized
        assert runtime_reader.call_count == 3

    def test_request_handoff_accepts_a_finalized_successor_when_tcp_ownership_lags(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor = process.OwnedProcess(999, ctx.executable, 0.5)
        owned = process.OwnedProcess(1000, ctx.executable, 1.0)
        tcp_attribution = mocker.patch.object(process, "listener_pids", return_value=[999])
        capture = mocker.patch.object(process, "wait_for_executable", return_value=owned)
        predecessor_alive = iter((True, False))
        alive = mocker.patch.object(
            process,
            "owned_process_alive",
            side_effect=lambda generation: (
                True if generation.pid == owned.pid else next(predecessor_alive)
            ),
        )
        mocker.patch.object(handoff, "post_ready", return_value=ready_ack(expected))
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 1.0, 100.0])
        mocker.patch.object(handoff.time, "sleep")

        result = handoff.request(
            ctx,
            expected,
            source_listener=predecessor,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(
                side_effect=(
                    idle_runtime(),
                    matching_health(1000, expected, handoff_state="finalized"),
                )
            ),
            timeout_seconds=1,
        )

        assert result["child_pid"] == owned.pid
        tcp_attribution.assert_not_called()
        capture.assert_called_once_with(
            1000,
            ctx.executable,
            roles={serving_roles.HANDOFF_CHILD_MODE},
            timeout_seconds=1,
        )
        assert {call.args[0].pid for call in alive.call_args_list} == {999, 1000}

    def test_request_handoff_allows_ready_until_the_configured_deadline(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor, _, _ = admit_handoff_generations(mocker, ctx)
        ready = mocker.patch.object(handoff, "post_ready", return_value=ready_ack(expected))

        handoff.request(
            ctx,
            expected,
            source_listener=predecessor,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(
                side_effect=(
                    idle_runtime(),
                    matching_health(1000, expected, handoff_state="finalized"),
                )
            ),
            timeout_seconds=30,
        )

        assert ready.call_args.kwargs["timeout_seconds"] == 30

    def test_request_handoff_rejects_invalid_listener_ownership_or_control_conflict(
        self, subtests, *, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        cases = (
            ("missing captured generation", False, None, "no longer active"),
            (
                "in-progress conflict",
                True,
                errors.InstallError("handoff control returned HTTP 409"),
                "409",
            ),
        )
        for name, generation_alive, failure, message in cases:
            with subtests.test(case=name):
                source_listener = process.OwnedProcess(999, ctx.executable, 0.5)
                mocker.patch.object(process, "owned_process_alive", return_value=generation_alive)
                mocker.patch.object(handoff, "post_ready", side_effect=failure)
                with pytest.raises(errors.InstallError, match=message):
                    handoff.request(
                        ctx,
                        expected,
                        source_listener=source_listener,
                        source_runtime=idle_runtime(),
                        runtime_reader=lambda _ctx: idle_runtime(),
                        timeout_seconds=1,
                    )

    def test_request_handoff_rejects_a_loopback_runtime_that_is_not_the_captured_source(
        self, *, mocker
    ) -> None:
        """Control must target the captured process generation and payload identity."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        posted = mocker.patch.object(handoff, "post_ready")

        with pytest.raises(errors.InstallError, match="no longer active"):
            handoff.request(
                ctx,
                expected_metadata(),
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(pid=1000),
                timeout_seconds=1,
            )

        posted.assert_not_called()

    def test_handoff_post_requires_a_complete_ready_acknowledgement(self, subtests, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        valid = ready_ack(expected)
        open_request = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response(valid),
        )
        assert handoff.post_ready(ctx, expected)["child_pid"] == 1000
        for field, bad_value in (
            ("ok", False),
            ("state", "preparing"),
            ("protocol_version", 1),
            ("transaction_id", "wrong"),
            ("child_pid", 0),
        ):
            with subtests.test(field=field):
                open_request.return_value = Response({**valid, field: bad_value})
                with pytest.raises(errors.InstallError):
                    handoff.post_ready(ctx, expected)

    def test_post_ready_bounds_timing_and_rejects_transport_or_response_failures(
        self, subtests, *, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        valid = json.dumps(ready_ack(expected)).encode()
        open_request = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response(valid),
        )
        handoff.post_ready(ctx, expected, lease_seconds=0.1, timeout_seconds=999)
        request = open_request.call_args.args[0]
        body = json.loads(request.data)
        assert body["lease_seconds"] == 1
        assert body["timeout_seconds"] == 120

        failures = (
            (Response(valid, status=500), "returned HTTP 500"),
            (Response(b"x" * (handoff._MAX_BODY_BYTES + 1)), "response is too large"),
            (Response(b"[]"), "invalid response"),
        )
        for response, message in failures:
            with subtests.test(message=message):
                open_request.side_effect = None
                open_request.return_value = response
                with pytest.raises(errors.InstallError, match=message):
                    handoff.post_ready(ctx, expected)

        conflict_body = io.BytesIO(b"conflict")
        for failure, message in (
            (
                urllib.error.HTTPError("url", 409, "conflict", Message(), conflict_body),
                "HTTP 409",
            ),
            (urllib.error.URLError("offline"), "unavailable"),
            (ValueError("bad timeout"), "unavailable"),
        ):
            with subtests.test(failure=type(failure).__name__):
                open_request.side_effect = failure
                with pytest.raises(errors.InstallError, match=message):
                    handoff.post_ready(ctx, expected)
        assert conflict_body.closed

        oversized = expected_metadata(release="x" * handoff._MAX_BODY_BYTES)
        with pytest.raises(errors.InstallError, match="request payload is too large"):
            handoff.post_ready(ctx, oversized)

    def test_request_handoff_rejects_a_wrong_child_pid_in_the_health_snapshot(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor, _, _ = admit_handoff_generations(mocker, ctx)
        mocker.patch.object(
            handoff,
            "post_ready",
            return_value=ready_ack(expected),
        )
        mocker.patch.object(handoff.time, "sleep")
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.0, 100.0])
        with pytest.raises(errors.InstallError):
            handoff.request(
                ctx,
                expected,
                source_listener=predecessor,
                source_runtime=idle_runtime(),
                runtime_reader=mocker.Mock(
                    side_effect=(
                        idle_runtime(),
                        matching_health(1000, expected, pid=4242),
                    )
                ),
                timeout_seconds=1,
            )

    def test_request_handoff_rejects_each_runtime_field_mismatch(self, subtests, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor, _, _ = admit_handoff_generations(mocker, ctx)
        mocker.patch.object(handoff, "post_ready", return_value={"child_pid": 1000})
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.0, 100.0])
        with pytest.raises(errors.InstallError, match="did not converge"):
            handoff.request(
                ctx,
                expected,
                source_listener=predecessor,
                source_runtime=idle_runtime(),
                runtime_reader=mocker.Mock(side_effect=(idle_runtime(), None)),
                timeout_seconds=1,
            )
        overrides = {
            "handoff_protocol_version": 1,
            "handoff_transaction_id": "wrong-txn",
            "release": "1.0.24",
            "serving_payload_sha256": "c" * 64,
            "release_receipt_sha256": "e" * 64,
            "payload_manifest_sha256": "d" * 64,
            "accepting": False,
            "draining": True,
            "handoff_state": "ready",
        }
        for field, bad_value in overrides.items():
            with subtests.test(field=field):
                runtime = matching_health(1000, expected, **{field: bad_value})
                mocker.patch.object(
                    handoff,
                    "post_ready",
                    return_value=ready_ack(expected),
                )
                mocker.patch.object(handoff.time, "sleep")
                mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.0, 100.0])
                with pytest.raises(errors.InstallError):
                    handoff.request(
                        ctx,
                        expected,
                        source_listener=predecessor,
                        source_runtime=idle_runtime(),
                        runtime_reader=mocker.Mock(side_effect=(idle_runtime(), runtime)),
                        timeout_seconds=1,
                    )

    def test_request_handoff_times_out_before_the_child_listener_converges(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        predecessor, _, _ = admit_handoff_generations(mocker, ctx)
        mocker.patch.object(
            handoff,
            "post_ready",
            return_value={"child_pid": 1000},
        )
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 1.0, 10.0])
        mocker.patch.object(handoff.time, "sleep")
        with pytest.raises(errors.InstallError, match="did not converge"):
            handoff.request(
                ctx,
                expected,
                source_listener=predecessor,
                source_runtime=idle_runtime(),
                runtime_reader=mocker.Mock(side_effect=(idle_runtime(), None)),
                timeout_seconds=1,
                lease_seconds=1,
            )
