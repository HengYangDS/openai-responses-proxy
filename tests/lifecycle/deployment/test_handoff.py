"""Installed same-payload reload wiring for the rolling-handoff protocol."""

from __future__ import annotations

import io
import json
import tempfile
import urllib.error
from collections.abc import Iterator
from contextlib import ExitStack
from email.message import Message
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health
from tests.serving.handoff.fixtures import ready_ack

ROOT = Path(__file__).resolve().parents[3]


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


class TestControllerHandoffWiring:
    def setup_method(self):
        self._cleanups = ExitStack()
        self.tempdir = tempfile.TemporaryDirectory()
        self._cleanups.callback(self.tempdir.cleanup)

    def teardown_method(self) -> None:
        self._cleanups.close()

    def test_capture_source_listener_requires_one_live_exact_product_generation(
        self, subtests, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        runtime = idle_runtime()
        admitted = process.OwnedProcess(999, ctx.executable, 0.5)
        capture = mocker.patch.object(process, "capture_executable", return_value=admitted)
        alive = mocker.patch.object(process, "owned_process_alive", return_value=True)

        assert handoff.capture_source_listener(ctx, runtime) == admitted
        capture.assert_called_once_with(
            999,
            ctx.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
        alive.assert_called_once_with(admitted)

        for pid, generation, generation_alive in (
            (True, admitted, True),
            (0, admitted, True),
            (999, None, True),
            (999, admitted, False),
        ):
            with subtests.test(
                pid=pid,
                generation=generation,
                generation_alive=generation_alive,
            ):
                runtime["pid"] = pid
                capture.return_value = generation
                alive.return_value = generation_alive
                with pytest.raises(errors.InstallError, match="not verified"):
                    handoff.capture_source_listener(ctx, runtime)

    def test_capture_source_listener_does_not_depend_on_platform_tcp_attribution(self, mocker):
        ctx = install_context(Path(self.tempdir.name))
        runtime = idle_runtime()
        admitted = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "capture_executable", return_value=admitted)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        tcp_attribution = mocker.patch.object(process, "listener_pids", return_value=[])
        stale_identity_read = mocker.patch.object(
            process, "verified_proxy_listener_pids", return_value=[]
        )

        assert handoff.capture_source_listener(ctx, runtime) == admitted
        tcp_attribution.assert_not_called()
        stale_identity_read.assert_not_called()

    def test_runtime_supports_handoff_requires_a_complete_available_identity(self, subtests):
        incomplete = idle_runtime()
        incomplete.pop("serving_payload_sha256")
        cases: list[tuple[dict[str, object] | None, bool]] = [
            (idle_runtime(handoff_capabilities=["selected-generation-handoff"]), True),
            (
                idle_runtime(
                    handoff_state="finalized",
                    handoff_transaction_id="txn-previous-finalized",
                    handoff_capabilities=["selected-generation-handoff"],
                ),
                True,
            ),
        ]
        unsupported: list[dict[str, object] | None] = [
            {"handoff_protocol_version": 1},
            {"handoff_protocol_version": 2},
            incomplete,
            idle_runtime(accepting=False),
            idle_runtime(draining=True),
            idle_runtime(handoff_state="ready"),
            idle_runtime(handoff_capabilities=["repeatable"]),
            {},
            None,
            {"release": "1.0.24"},
            idle_runtime(
                handoff_state="finalized",
                handoff_transaction_id="txn-legacy-finalized",
                handoff_capabilities=None,
            ),
        ]
        cases.extend((runtime, False) for runtime in unsupported)
        for runtime, supported in cases:
            with subtests.test(runtime=runtime, supported=supported):
                assert handoff.runtime_supports_handoff(runtime) is supported

    def test_deployment_strategy_requires_complete_identity_and_explicit_repeatability(
        self, subtests
    ) -> None:
        finalized = idle_runtime(
            handoff_state="finalized",
            handoff_transaction_id="txn-previous-finalized",
        )
        cases = (
            (
                idle_runtime(
                    handoff_capabilities=[
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                        handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
                    ]
                ),
                "handoff",
            ),
            (
                {
                    **finalized,
                    "handoff_capabilities": [
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                        handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
                    ],
                },
                "handoff",
            ),
            (
                idle_runtime(
                    handoff_capabilities=[
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY
                    ]
                ),
                "native_generation",
            ),
            (
                {**finalized, "handoff_capabilities": ["repeatable"]},
                "native_generation",
            ),
            ({**finalized, "handoff_capabilities": None}, "native_generation"),
            ({**finalized, "serving_payload_sha256": None}, "unsupported"),
            ({**finalized, "accepting": False}, "unsupported"),
        )

        for runtime, expected in cases:
            with subtests.test(runtime=runtime):
                assert handoff.deployment_strategy(runtime) == expected

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

    def test_native_replacement_drains_only_the_captured_predecessor(self, *, mocker) -> None:
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        runtime_reader = mocker.Mock(
            side_effect=(
                idle_runtime(),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=2,
                    active_handlers=5,
                ),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=0,
                    active_handlers=2,
                ),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=0,
                    active_handlers=1,
                ),
            )
        )
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True, "active_responses": 2}, status=200),
        )
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "listener_pids", return_value=[999])

        handoff.drain_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=runtime_reader,
            timeout_seconds=1,
        )

        request = opened.call_args.args[0]
        assert request.full_url.endswith("/control/drain")
        assert request.method == "POST"
        assert request.headers["X-openai-responses-proxy-drain-lease-seconds"] == "2"
        assert runtime_reader.call_count == 4

    def test_failed_native_replacement_reopens_the_captured_predecessor(self, *, mocker) -> None:
        """A failed replacement must not leave the surviving listener drained."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": False}, status=200),
        )
        mocker.patch.object(process, "owned_process_alive", return_value=True)

        assert handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(
                side_effect=(
                    idle_runtime(draining=True, accepting=False),
                    idle_runtime(),
                )
            ),
        )

        request = opened.call_args.args[0]
        assert request.full_url.endswith("/control/drain")
        assert request.method == "DELETE"

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (Response({}, status=500), "HTTP 500"),
            (
                Response(b"x" * (handoff._MAX_BODY_BYTES + 1), status=200),
                "response is too large",
            ),
            (Response(b"[]", status=200), "did not reopen Responses admission"),
            (urllib.error.URLError("offline"), "unavailable"),
            (ValueError("bad timeout"), "unavailable"),
        ],
    )
    def test_failed_native_replacement_rejects_invalid_resume_control(
        self, response: object, message: str, *, mocker
    ) -> None:
        """Rollback admission must be acknowledged by the captured predecessor."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        opened = mocker.patch.object(handoff.loopback, "open_request")
        if isinstance(response, BaseException):
            opened.side_effect = response
        else:
            opened.return_value = response

        with pytest.raises(errors.InstallError, match=message):
            handoff.resume_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
            )

    def test_failed_native_replacement_requires_the_same_runtime_after_resume(
        self, *, mocker
    ) -> None:
        """A changed generation after DELETE cannot prove restored admission."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": False}, status=200),
        )

        assert not handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(side_effect=(idle_runtime(), None)),
        )

    def test_failed_native_replacement_does_not_reopen_a_replaced_listener(self, *, mocker) -> None:
        """A successor that already owns the port must not receive predecessor control."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=False)
        opened = mocker.patch.object(handoff.loopback, "open_request")

        assert not handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=lambda _ctx: None,
        )

        opened.assert_not_called()

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (Response({}, status=500), "HTTP 500"),
            (
                Response(b"x" * (handoff._MAX_BODY_BYTES + 1), status=200),
                "response is too large",
            ),
            (Response(b"[]", status=200), "did not close Responses admission"),
            (urllib.error.URLError("offline"), "unavailable"),
            (ValueError("bad timeout"), "unavailable"),
        ],
    )
    def test_native_replacement_rejects_invalid_drain_control(
        self, response: object, message: str, *, mocker
    ) -> None:
        """Native replacement proceeds only after an exact drain acknowledgement."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        opened = mocker.patch.object(handoff.loopback, "open_request")
        if isinstance(response, BaseException):
            opened.side_effect = response
        else:
            opened.return_value = response

        with pytest.raises(errors.InstallError, match=message):
            handoff.drain_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
                timeout_seconds=1,
            )

    def test_native_replacement_rejects_unproved_or_changed_predecessor(
        self, subtests, *, mocker
    ) -> None:
        """Drain remains bound to the captured process generation until completion."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True}, status=200),
        )

        with subtests.test(case="not-admitted"):
            mocker.patch.object(process, "owned_process_alive", return_value=False)
            with pytest.raises(errors.InstallError, match="no longer active"):
                handoff.drain_responses(
                    ctx,
                    source_listener=source,
                    source_runtime=idle_runtime(),
                    runtime_reader=lambda _ctx: None,
                    timeout_seconds=1,
                )
            opened.assert_not_called()

        with subtests.test(case="changed-during-drain"):
            mocker.patch.object(process, "owned_process_alive", return_value=True)
            runtime_reader = mocker.Mock(side_effect=(idle_runtime(), None))
            with pytest.raises(errors.InstallError, match="changed while draining"):
                handoff.drain_responses(
                    ctx,
                    source_listener=source,
                    source_runtime=idle_runtime(),
                    runtime_reader=runtime_reader,
                    timeout_seconds=1,
                )
            assert runtime_reader.call_count == 2

    def test_native_replacement_drain_timeout_is_bounded(self, *, mocker) -> None:
        """A predecessor that never reaches zero active Responses fails at the bound."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True}, status=200),
        )
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.0, 2.0])
        mocker.patch.object(handoff.time, "sleep")

        with pytest.raises(errors.InstallError, match="within 1s"):
            handoff.drain_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: matching_health(
                    999,
                    {
                        **expected_metadata(),
                        "release": "1.0.24",
                        "release_receipt_sha256": "e" * 64,
                    },
                    draining=True,
                    accepting=False,
                    active_responses=1,
                ),
                timeout_seconds=1,
            )

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

    def test_expected_metadata_reads_and_validates_each_release_identity(self, *, mocker):
        root = Path(self.tempdir.name)
        manifest_path = root / inventory.MANIFEST_FILENAME

        with pytest.raises(errors.InstallError, match="payload files are unavailable") as raised:
            handoff.expected_metadata(str(root))
        assert str(root) not in str(raised.value)

        for payload in (
            b"not-json",
            b"{}",
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "bad",
                    "release_receipt_sha256": "f" * 64,
                }
            ).encode(),
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "BAD",
                }
            ).encode(),
            json.dumps(
                {
                    "release": "",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "f" * 64,
                }
            ).encode(),
        ):
            manifest_path.write_bytes(payload)
            with pytest.raises(errors.InstallError):
                handoff.expected_metadata(str(root))

        manifest_path.write_text(
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "f" * 64,
                }
            ),
            encoding="utf-8",
        )
        (root / "VERSION").write_text("0.0.1\n", encoding="utf-8")
        mocker.patch.object(handoff.uuid, "uuid4", return_value=mocker.Mock(hex="txn-fixed"))
        metadata = handoff.expected_metadata(str(root))
        assert metadata["transaction_id"] == "txn-fixed"
        assert metadata["release"] == "1.0.25"
        assert metadata["manifest_sha256"] == handoff.digest.sha256_file(manifest_path)

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

    def test_wait_for_rollback_accepts_only_the_exact_resumed_runtime_and_times_out(
        self, *, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        old = idle_runtime()
        mismatches = (
            None,
            idle_runtime(pid=123),
            idle_runtime(serving_payload_sha256="c" * 64),
            idle_runtime(release_receipt_sha256="c" * 64),
            idle_runtime(payload_manifest_sha256="c" * 64),
            idle_runtime(handoff_protocol_version=1),
            idle_runtime(handoff_state="finalized"),
            idle_runtime(handoff_transaction_id="txn"),
            idle_runtime(accepting=False),
            idle_runtime(draining=True),
        )
        runtimes = iter((*mismatches, old))
        listeners = ([123], *([[999]] * (len(mismatches) + 1)))
        mocker.patch.object(process, "verified_proxy_listener_pids", side_effect=listeners)
        mocker.patch.object(handoff.time, "monotonic", side_effect=range(20))
        mocker.patch.object(handoff.time, "sleep")
        assert (
            handoff.wait_for_rollback(
                ctx, old, runtime_reader=lambda _ctx: next(runtimes), timeout_seconds=20
            )
            == old
        )
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[1000])
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.1, 0.2])
        mocker.patch.object(handoff.time, "sleep")

        with pytest.raises(errors.InstallError, match="did not resume"):
            handoff.wait_for_rollback(
                ctx,
                old,
                runtime_reader=lambda _ctx: None,
                timeout_seconds=0.15,
            )

    def test_failure_resolver_classifies_finalized_rolled_back_and_unknown_states(
        self, subtests, *, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        old = idle_runtime()
        finalized = matching_health(1000, expected, pid=1000, handoff_state="finalized")
        cases = (
            ("finalized", [1000], finalized, ("finalized", finalized)),
            ("rolled_back", [999], old, ("rolled_back", old)),
        )
        for name, listeners, runtime, expected_result in cases:
            with subtests.test(name=name):
                mocker.patch.object(process, "verified_proxy_listener_pids", return_value=listeners)
                predecessor = process.OwnedProcess(999, ctx.executable, 0.5)
                successor = process.OwnedProcess(1000, ctx.executable, 1.0)
                mocker.patch.object(
                    process,
                    "capture_executable",
                    return_value=successor if name == "finalized" else None,
                )
                mocker.patch.object(
                    process,
                    "owned_process_alive",
                    side_effect=lambda generation, case=name, successor_pid=successor.pid: (
                        generation.pid == successor_pid if case == "finalized" else True
                    ),
                )
                mocker.patch.object(process, "listener_pids", return_value=listeners)
                assert (
                    handoff.resolve_after_controller_failure(
                        ctx,
                        old,
                        expected,
                        runtime_reader=lambda _ctx, snapshot=runtime: snapshot,
                        timeout_seconds=1,
                        lease_seconds=1,
                        source_listener=predecessor,
                    )
                    == expected_result
                )
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[999])
        predecessor = process.OwnedProcess(999, ctx.executable, 0.5)
        successor = process.OwnedProcess(1000, ctx.executable, 1.0)
        mocker.patch.object(process, "capture_executable", return_value=successor)
        mocker.patch.object(
            process,
            "owned_process_alive",
            side_effect=lambda generation: generation.pid == successor.pid,
        )
        assert handoff.resolve_after_controller_failure(
            ctx,
            old,
            expected,
            runtime_reader=lambda _ctx: finalized,
            timeout_seconds=1,
            lease_seconds=1,
            source_listener=predecessor,
        ) == ("finalized", finalized)

    def test_failure_resolver_ignores_non_mapping_and_non_integer_runtime_pids(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        expected = expected_metadata()
        old = idle_runtime()
        invalid_runtime: dict[str, object] = {"pid": True}
        runtimes: Iterator[dict[str, object] | None] = iter(
            (
                None,
                invalid_runtime,
                matching_health(1000, expected, pid=1000, handoff_state="finalized"),
            )
        )
        mocker.patch.object(
            process,
            "verified_proxy_listener_pids",
            side_effect=[[999, 1000], [999, 1000], [1000]],
        )
        successor = process.OwnedProcess(1000, ctx.executable, 1.0)
        mocker.patch.object(process, "capture_executable", return_value=successor)
        predecessor = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(
            process,
            "owned_process_alive",
            side_effect=lambda generation: generation.pid == successor.pid,
        )
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 3.0])
        mocker.patch.object(handoff.time, "sleep")
        assert (
            handoff.resolve_after_controller_failure(
                ctx,
                old,
                expected,
                runtime_reader=lambda _ctx: next(runtimes),
                timeout_seconds=10,
                lease_seconds=1,
                source_listener=predecessor,
            )[0]
            == "finalized"
        )

    def test_digest_helpers_reject_noncanonical_values_and_hash_large_files(self, subtests):
        for value in (None, "a" * 63, "g" * 64, "A" * 64):
            with subtests.test(value=value):
                assert not handoff.digest.is_sha256(value)
        assert handoff.digest.is_sha256("a" * 64)

        path = Path(self.tempdir.name) / "large.bin"
        path.write_bytes(b"a" * (1024 * 1024 + 1))
        assert len(handoff.digest.sha256_file(path)) == 64

    def test_reload_uses_handoff_without_terminating_the_old_pid_when_supported(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        native = mocker.patch.object(control, "adapter").return_value
        native.configured_executable.return_value = ctx.executable
        mocker.patch.object(control, "read_runtime", return_value=idle_runtime())
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection, "verify_payload_manifest", return_value=(True, "ok")
        )
        mocker.patch.object(handoff, "expected_metadata", return_value=expected_metadata())
        request_handoff = mocker.patch.object(
            handoff,
            "request",
            return_value={"old_pid": 1, "child_pid": 2, "release": "1.0.25"},
        )
        mocker.patch.object(
            handoff,
            "capture_source_listener",
            return_value=process.OwnedProcess(999, ctx.executable, 0.5),
        )
        terminate = mocker.patch.object(process, "terminate_pid")
        result = control.reload(ctx, timeout_seconds=5)
        request_handoff.assert_called_once()
        terminate.assert_not_called()
        assert result == {"state": "reloaded", "old_pid": 1, "new_pid": 2}

    def test_reload_rejects_handoff_when_installed_payload_integrity_fails(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        mocker.patch.object(control, "read_runtime", return_value={"handoff_protocol_version": 2})
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection,
            "verify_payload_manifest",
            return_value=(False, "hash mismatch"),
        )
        request_handoff = mocker.patch.object(handoff, "request")
        with pytest.raises(errors.InstallError, match="integrity"):
            control.reload(ctx, timeout_seconds=5)
        request_handoff.assert_not_called()

    def test_reload_recovers_a_finalized_result_after_controller_failure(self, *, mocker):
        ctx = install_context(Path(self.tempdir.name))
        Path(ctx.install_dir).mkdir(parents=True)
        native = mocker.patch.object(control, "adapter").return_value
        native.configured_executable.return_value = ctx.executable
        expected = expected_metadata()
        old = idle_runtime()
        finalized = matching_health(1000, expected, pid=1000, handoff_state="finalized")
        mocker.patch.object(control, "read_runtime", return_value=old)
        mocker.patch.object(handoff, "runtime_supports_handoff", return_value=True)
        mocker.patch.object(
            payload_projection, "verify_payload_manifest", return_value=(True, "ok")
        )
        mocker.patch.object(handoff, "expected_metadata", return_value=expected)
        mocker.patch.object(handoff, "request", side_effect=KeyboardInterrupt())
        mocker.patch.object(
            handoff,
            "capture_source_listener",
            return_value=process.OwnedProcess(999, ctx.executable, 0.5),
        )
        mocker.patch.object(
            handoff,
            "resolve_after_controller_failure",
            return_value=("finalized", finalized),
        )
        result = control.reload(ctx, timeout_seconds=5)
        assert result["new_pid"] == 1000
        assert result["recovered_after_controller_failure"]
