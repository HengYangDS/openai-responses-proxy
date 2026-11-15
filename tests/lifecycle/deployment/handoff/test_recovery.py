"""Controller-loss classification and exact rollback convergence."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import control as listener_control
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest
    from pytest_mock import MockerFixture


_REFUSED = handoff.HandoffRefusedError("handoff control returned HTTP 409")
_UNANSWERED = errors.InstallError("handoff control is unavailable")


class TestControllerHandoffWiring(HandoffFixture):
    def test_resumed_runtime_requires_the_exact_old_identity_and_admission(self) -> None:
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
        for refused in (False, True):
            for runtime in mismatches:
                assert not handoff._old_runtime_resumed(runtime, old, refused=refused)
            assert handoff._old_runtime_resumed(old, old, refused=refused)

    def test_a_resting_handoff_child_is_resumed_only_after_it_refused_the_request(self) -> None:
        """A listener that a handoff created reports that finalized session until its next one.

        Only its refusal says that the request will not begin: without that answer the listener
        may still be about to begin it.
        """
        resting = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
        assert handoff._old_runtime_resumed(dict(resting), resting, refused=True)
        assert not handoff._old_runtime_resumed(dict(resting), resting, refused=False)
        for refused in (False, True):
            # It began the handoff and rolled it back.
            assert handoff._old_runtime_resumed(idle_runtime(), resting, refused=refused)
            for runtime in (
                idle_runtime(handoff_state="preparing", handoff_transaction_id="8" * 32),
                idle_runtime(handoff_state="finalized", handoff_transaction_id="8" * 32),
                idle_runtime(handoff_state="finalized"),
                idle_runtime(handoff_state="aborting"),
                idle_runtime(
                    handoff_state="finalized", handoff_transaction_id="7" * 32, accepting=False
                ),
            ):
                assert not handoff._old_runtime_resumed(runtime, resting, refused=refused)
            # A capture that was idle admits no finalized session.
            assert not handoff._old_runtime_resumed(resting, idle_runtime(), refused=refused)

    def test_a_capture_that_names_no_finished_session_admits_only_an_idle_one(self) -> None:
        """An echoed capture counts only as a finalized session with its transaction."""
        for state, transaction in (
            ("finalized", None),
            ("finalized", ""),
            ("finalized", 7),
            ("preparing", "7" * 32),
            ("ready", "7" * 32),
            ("committing", "7" * 32),
        ):
            captured = idle_runtime(handoff_state=state, handoff_transaction_id=transaction)
            assert not handoff._old_runtime_resumed(dict(captured), captured, refused=True)
            assert handoff._old_runtime_resumed(idle_runtime(), captured, refused=True)

    def test_failure_resolver_waits_out_a_resting_child_that_sent_no_answer(
        self, *, mocker: MockerFixture
    ) -> None:
        """A request without an answer may still begin, so its listener at rest resolves nothing."""
        ctx = install_context(Path(self.tempdir.name))
        resting = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        clock = mocker.patch.object(handoff.time, "monotonic")
        sleep = mocker.patch.object(handoff.time, "sleep")
        reader = mocker.Mock(return_value=dict(resting))

        for failure in (_UNANSWERED, KeyboardInterrupt()):
            reader.reset_mock()
            sleep.reset_mock()
            # The deadline is 1 + 1 + 5 seconds after the first reading of the clock.
            clock.side_effect = [0.0, 0.0, 6.9, 7.0]
            assert handoff.resolve_after_controller_failure(
                ctx,
                resting,
                expected_metadata(),
                failure=failure,
                runtime_reader=reader,
                timeout_seconds=1,
                lease_seconds=1,
                source_listener=process.OwnedProcess(999, ctx.executable, 0.5),
            ) == ("unknown", None)
            assert reader.call_count == 2
            assert sleep.call_count == 2

    def test_a_request_that_a_resting_listener_has_read_and_not_begun_resolves_nothing(
        self, *, mocker: MockerFixture
    ) -> None:
        """The order that the rule answers, with the listener's own handler and session.

        The handler has compared the request with the disk and has not begun the handoff:
        health still shows the finished session that the controller captured. A controller
        that lost its answer in that moment must not call the handoff rolled back, because
        the handler goes on and begins it.
        """
        bindings = listener_control.Bindings(
            runtime_status=entrypoint_module.runtime_status,
            handoff_context=entrypoint_module._handoff_context,
        )
        session = handoff_module._HANDOFF_SESSION

        def health() -> dict[str, object]:
            identity = handoff_module.runtime_identity(bindings.handoff_context())
            return idle_runtime(
                handoff_state=identity["handoff_state"],
                handoff_transaction_id=identity["handoff_transaction_id"],
            )

        seen: dict[str, dict[str, object]] = {}

        def begin(*_args: object, **_kwargs: object) -> None:
            # Where the handler calls prepare, nothing has begun.
            seen["read"] = health()
            # prepare then replaces the finished session for good, as its first step.
            with handoff_module._HANDOFF_LOCK:
                handoff_module.reset_session_to_idle()
                handoff_module._transition("preparing")
                session.update(transaction_id=expected_metadata()["transaction_id"])
            seen["begun"] = health()
            raise handoff_module.HandoffError("the test stops the handoff here")

        handoff_module.reset_session_to_idle()
        try:
            # A listener that a handoff created.
            with handoff_module._HANDOFF_LOCK:
                session.update(state="finalized", transaction_id="7" * 32)
            captured = health()
            mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
            mocker.patch.object(handoff_module, "prepare", side_effect=begin)
            listener_control.prepare_handoff(
                fake_handler(expected_metadata(), mocker=mocker), bindings
            )
        finally:
            handoff_module.reset_session_to_idle()

        assert (captured["handoff_state"], captured["handoff_transaction_id"]) == (
            "finalized",
            "7" * 32,
        )
        assert seen["read"] == captured
        assert not handoff._old_runtime_resumed(seen["read"], captured, refused=False)
        assert seen["begun"]["handoff_state"] == "preparing"
        for refused in (False, True):
            assert not handoff._old_runtime_resumed(seen["begun"], captured, refused=refused)

    def test_failure_resolver_answers_at_once_for_a_resting_child_that_refused_the_request(
        self, *, mocker: MockerFixture
    ) -> None:
        """A request that the listener refused before it began leaves nothing to wait for."""
        ctx = install_context(Path(self.tempdir.name))
        resting = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "capture_executable", return_value=None)
        sleep = mocker.patch.object(
            handoff.time, "sleep", side_effect=AssertionError("the resolver waited")
        )
        reader = mocker.Mock(return_value=dict(resting))

        assert handoff.resolve_after_controller_failure(
            ctx,
            resting,
            expected_metadata(),
            failure=_REFUSED,
            runtime_reader=reader,
            timeout_seconds=30,
            lease_seconds=900,
            source_listener=process.OwnedProcess(999, ctx.executable, 0.5),
        ) == ("rolled_back", resting)
        reader.assert_called_once_with(ctx)
        sleep.assert_not_called()

    def test_predecessor_serves_only_as_the_captured_generation_with_open_admission(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        """The proof reads runtime evidence and sends no control request."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        alive = mocker.patch.object(process, "owned_process_alive", return_value=True)
        control_request = mocker.patch.object(handoff.loopback, "open_request")

        def serves(runtime: dict[str, object] | None) -> bool:
            return handoff.predecessor_serves(
                ctx,
                failure=_UNANSWERED,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: runtime,
            )

        assert serves(idle_runtime())
        for runtime in (
            None,
            idle_runtime(pid=1000),
            idle_runtime(release="1.0.25"),
            idle_runtime(serving_payload_sha256="c" * 64),
            idle_runtime(accepting=False),
            idle_runtime(draining=True),
            idle_runtime(handoff_state="preparing", handoff_transaction_id="7" * 32),
            idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32),
        ):
            with subtests.test(runtime=runtime):
                assert not serves(runtime)
        resting = idle_runtime(handoff_state="finalized", handoff_transaction_id="7" * 32)

        def rests(runtime: dict[str, object], failure: BaseException) -> bool:
            return handoff.predecessor_serves(
                ctx,
                failure=failure,
                source_listener=source,
                source_runtime=resting,
                runtime_reader=lambda _ctx: runtime,
            )

        # Its finished session proves nothing begun only with the listener's refusal.
        assert rests(dict(resting), _REFUSED)
        assert not rests(dict(resting), _UNANSWERED)
        assert not rests(dict(resting), KeyboardInterrupt())
        for failure in (_REFUSED, _UNANSWERED):
            assert rests(idle_runtime(), failure)
            assert not rests(
                idle_runtime(handoff_state="ready", handoff_transaction_id="8" * 32), failure
            )
        alive.return_value = False
        assert not serves(idle_runtime())
        control_request.assert_not_called()

    def test_failure_resolver_classifies_finalized_rolled_back_and_unknown_states(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
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
                        failure=_UNANSWERED,
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
            failure=_UNANSWERED,
            runtime_reader=lambda _ctx: finalized,
            timeout_seconds=1,
            lease_seconds=1,
            source_listener=predecessor,
        ) == ("finalized", finalized)

    def test_failure_resolver_ignores_non_mapping_and_non_integer_runtime_pids(
        self, *, mocker: MockerFixture
    ) -> None:
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
                failure=_UNANSWERED,
                runtime_reader=lambda _ctx: next(runtimes),
                timeout_seconds=10,
                lease_seconds=1,
                source_listener=predecessor,
            )[0]
            == "finalized"
        )
