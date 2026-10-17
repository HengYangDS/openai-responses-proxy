"""Controller-loss classification and exact rollback convergence."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health


class TestControllerHandoffWiring(HandoffFixture):
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
