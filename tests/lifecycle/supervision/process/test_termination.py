"""Bounded generation-aware termination and exit evidence."""

from __future__ import annotations

import os
from dataclasses import astuple

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import process


class TestTermination:
    @pytest.mark.parametrize(
        ("phase", "failure", "reason"),
        [
            ("capture", process.psutil.AccessDenied(123), "access denied"),
            ("generation", TypeError("private observation"), "invalid native observation"),
            ("status", process.psutil.AccessDenied(123), "access denied"),
            ("signal", PermissionError(5, "private path and token"), "OS error 5"),
            ("wait", process.psutil.AccessDenied(123), "access denied"),
        ],
    )
    def test_unproved_termination_preserves_safe_failure_phase(
        self, phase, failure, reason, *, mocker
    ) -> None:
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_RUNNING
        constructor = mocker.patch.object(process.psutil, "Process", return_value=candidate)
        if phase == "capture":
            constructor.side_effect = failure
        else:
            operation = {
                "generation": candidate.create_time,
                "status": candidate.status,
                "signal": candidate.terminate,
                "wait": candidate.wait,
            }[phase]
            operation.side_effect = failure

        with pytest.raises(errors.InstallError, match=phase) as raised:
            process.terminate_owned_process(
                process.OwnedProcess(123, "/private/installed/proxy", 42.0),
                timeout_seconds=1.0,
            )

        assert reason in str(raised.value)
        assert "private" not in str(raised.value)
        if phase in {"capture", "generation", "status"}:
            candidate.terminate.assert_not_called()

    def test_timeout_is_unproved_exit_not_a_claim_of_a_survivor(self, *, mocker) -> None:
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_RUNNING
        candidate.wait.side_effect = process.psutil.TimeoutExpired(1, 123)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        with pytest.raises(errors.InstallError, match=r"wait.*deadline expired") as raised:
            process.terminate_owned_process(
                process.OwnedProcess(123, "/private/installed/proxy", 42.0),
                timeout_seconds=1.0,
            )

        assert "exit is unproved" in str(raised.value)
        assert "still alive" not in str(raised.value)
        candidate.wait.assert_called_once_with(timeout=1.0)

    def test_captured_native_identity_survives_later_argv_denial(self, *, mocker):
        candidate = mocker.Mock()
        candidate.pid = 123
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_RUNNING
        candidate.cmdline.side_effect = [
            ["/installed/openai-responses-proxy", "--internal-handoff-child"],
            process.psutil.AccessDenied(123),
        ]
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        owned = process.capture_executable(
            123,
            "/installed/openai-responses-proxy",
            roles={"--internal-handoff-child"},
        )

        assert owned is not None
        assert astuple(owned) == (
            123,
            os.path.normcase(os.path.realpath("/installed/openai-responses-proxy")),
            42.0,
        )
        assert process.terminate_owned_process(owned, timeout_seconds=1.0)
        candidate.terminate.assert_called_once_with()
        candidate.wait.assert_called_once_with(timeout=1.0)

    def test_protocol_proven_pid_capture_does_not_depend_on_native_argv(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.cmdline.side_effect = process.psutil.AccessDenied(123)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        owned = process.capture_generation(123, "/installed/openai-responses-proxy")

        assert owned is not None
        assert astuple(owned) == (
            123,
            os.path.normcase(os.path.realpath("/installed/openai-responses-proxy")),
            42.0,
        )

    def test_captured_native_identity_failure_edges_are_portable(self, *, mocker):
        denied = mocker.Mock()
        denied.cmdline.return_value = ["/foreign/proxy", "--internal-handoff-child"]
        exited = mocker.Mock()
        exited.create_time.side_effect = process.psutil.NoSuchProcess(123)
        empty = mocker.Mock(pid=7)
        empty.cmdline.return_value = []
        inaccessible = mocker.Mock(pid=8)
        inaccessible.cmdline.side_effect = process.psutil.AccessDenied(8)

        constructor = mocker.patch.object(
            process.psutil,
            "Process",
            side_effect=[denied, exited],
        )
        assert (
            process.capture_executable(
                123,
                "/installed/openai-responses-proxy",
                roles={"--internal-handoff-child"},
            )
            is None
        )
        assert process.terminate_owned_process(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)
        )
        assert constructor.call_count == 2

        mocker.patch.object(process.psutil, "process_iter", return_value=[empty, inaccessible])
        assert process._process_inventory() == []

    def test_captured_native_identity_rejects_pid_reuse(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 43.0
        mocker.patch.object(process.psutil, "Process", return_value=candidate)
        owned = process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)

        with pytest.raises(errors.InstallError, match="generation: captured identity changed"):
            process.terminate_owned_process(owned, timeout_seconds=1.0)
        candidate.terminate.assert_not_called()

    def test_captured_native_identity_observation_is_fail_closed(self, subtests, *, mocker):
        owned = process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)
        for case, failure in (
            ("capture", process.psutil.AccessDenied(123)),
            ("alive", process.psutil.NoSuchProcess(123)),
            ("terminate", process.psutil.TimeoutExpired(123, 1)),
        ):
            with subtests.test(case=case):
                candidate = mocker.Mock()
                candidate.cmdline.return_value = [
                    "/installed/openai-responses-proxy",
                    "--internal-handoff-child",
                ]
                candidate.create_time.return_value = 42.0
                candidate.status.return_value = process.psutil.STATUS_RUNNING
                if case == "terminate":
                    candidate.wait.side_effect = failure
                constructor = (
                    mocker.patch.object(process.psutil, "Process", return_value=candidate)
                    if case == "terminate"
                    else mocker.patch.object(process.psutil, "Process", side_effect=failure)
                )

                if case == "capture":
                    assert (
                        process.capture_executable(123, "/installed/openai-responses-proxy") is None
                    )
                elif case == "alive":
                    assert not process.owned_process_alive(owned)
                else:
                    with pytest.raises(errors.InstallError, match="wait: deadline expired"):
                        process.terminate_owned_process(owned, timeout_seconds=1.0)
                    candidate.terminate.assert_called_once_with()
                constructor.assert_called_once_with(123)
                mocker.stopall()

    def test_captured_native_identity_reports_the_same_running_generation(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_RUNNING
        candidate.is_running.return_value = True
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.owned_process_alive(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)
        )

    def test_captured_native_identity_treats_a_zombie_as_exited(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_ZOMBIE
        candidate.is_running.return_value = True
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert not process.owned_process_alive(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)
        )

    def test_bounded_termination_accepts_an_adopted_zombie_tombstone(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.side_effect = [
            process.psutil.STATUS_RUNNING,
            process.psutil.STATUS_ZOMBIE,
        ]
        candidate.wait.side_effect = process.psutil.TimeoutExpired(123, 1)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_owned_process(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0),
            timeout_seconds=1.0,
        )
        candidate.terminate.assert_called_once_with()

    def test_bounded_termination_accepts_an_already_zombie_generation(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.return_value = process.psutil.STATUS_ZOMBIE
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_owned_process(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0)
        )
        candidate.terminate.assert_not_called()

    def test_bounded_termination_observes_disappearance_after_timeout(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.side_effect = [42.0, process.psutil.NoSuchProcess(123)]
        candidate.status.return_value = process.psutil.STATUS_RUNNING
        candidate.wait.side_effect = process.psutil.TimeoutExpired(123, 1)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_owned_process(
            process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0),
            timeout_seconds=1.0,
        )

    def test_bounded_termination_fails_closed_when_timeout_state_is_unreadable(self, *, mocker):
        candidate = mocker.Mock()
        candidate.create_time.return_value = 42.0
        candidate.status.side_effect = [
            process.psutil.STATUS_RUNNING,
            process.psutil.AccessDenied(123),
        ]
        candidate.wait.side_effect = process.psutil.TimeoutExpired(123, 1)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        with pytest.raises(errors.InstallError, match="timeout observation: access denied"):
            process.terminate_owned_process(
                process.OwnedProcess(123, "/installed/openai-responses-proxy", 42.0),
                timeout_seconds=1.0,
            )

    def test_script_termination_rechecks_identity_and_waits(self, *, mocker):
        mocker.patch.object(process, "pid_names_path", side_effect=[True, True])
        candidate = mocker.Mock()
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_pid(123, expected_path="/proxy.py", timeout_seconds=1.0)
        candidate.terminate.assert_called_once_with()
        candidate.wait.assert_called_once_with(timeout=1.0)

    def test_script_termination_fails_closed(self, subtests, *, mocker):
        cases = (
            ("identity", [False], None),
            ("identity changed", [True, False], None),
            ("wait", [True, True], process.psutil.TimeoutExpired(123, 1)),
        )
        for case, identities, failure in cases:
            with subtests.test(case=case):
                mocker.patch.object(process, "pid_names_path", side_effect=identities)
                candidate = mocker.Mock()
                if failure is not None:
                    candidate.wait.side_effect = failure
                constructor = mocker.patch.object(process.psutil, "Process", return_value=candidate)
                if failure is None:
                    assert not process.terminate_pid(
                        123, expected_path="/proxy.py", timeout_seconds=1.0
                    )
                else:
                    with pytest.raises(errors.InstallError, match="wait: deadline expired"):
                        process.terminate_pid(123, expected_path="/proxy.py", timeout_seconds=1.0)
                constructor_calls = 0 if case == "identity" else 1
                assert constructor.call_count == constructor_calls
                mocker.stopall()

    def test_native_termination_rechecks_identity_and_waits(self, *, mocker):
        roles = {"--internal-listener"}
        mocker.patch.object(
            process,
            "pid_names_executable",
            side_effect=[True, True],
        )
        candidate = mocker.Mock()
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_executable(
            123,
            "/installed/openai-responses-proxy",
            roles=roles,
            timeout_seconds=1.0,
        )
        candidate.terminate.assert_called_once_with()
        candidate.wait.assert_called_once_with(timeout=1.0)

    def test_native_termination_accepts_already_exited_identity(self, *, mocker):
        mocker.patch.object(
            process,
            "pid_names_executable",
            side_effect=[True, True],
        )
        candidate = mocker.Mock()
        candidate.wait.side_effect = process.psutil.NoSuchProcess(123)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_executable(123, "/installed/openai-responses-proxy")

    def test_native_termination_fails_closed(self, subtests, *, mocker):
        for case, identities, constructor_failure, wait_failure in (
            ("initial identity", [False], None, None),
            ("reused pid", [True, False], None, None),
            ("constructor", [True], process.psutil.AccessDenied(123), None),
            ("wait", [True, True], None, process.psutil.TimeoutExpired(123, 1)),
        ):
            with subtests.test(case=case):
                mocker.patch.object(
                    process,
                    "pid_names_executable",
                    side_effect=identities,
                )
                candidate = mocker.Mock()
                candidate.wait.side_effect = wait_failure
                constructor = mocker.patch.object(
                    process.psutil,
                    "Process",
                    side_effect=constructor_failure or [candidate],
                )
                if constructor_failure is None and wait_failure is None:
                    assert not process.terminate_executable(
                        123, "/installed/openai-responses-proxy", timeout_seconds=1.0
                    )
                else:
                    expected = (
                        "capture: access denied"
                        if constructor_failure
                        else "wait: deadline expired"
                    )
                    with pytest.raises(errors.InstallError, match=expected):
                        process.terminate_executable(
                            123, "/installed/openai-responses-proxy", timeout_seconds=1.0
                        )
                if case == "initial identity":
                    constructor.assert_not_called()
                mocker.stopall()

    def test_script_termination_accepts_already_exited_identity(self, *, mocker):
        mocker.patch.object(process, "pid_names_path", side_effect=[True, True])
        candidate = mocker.Mock()
        candidate.wait.side_effect = process.psutil.NoSuchProcess(123)
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.terminate_pid(123, expected_path="/installed/proxy.py")
