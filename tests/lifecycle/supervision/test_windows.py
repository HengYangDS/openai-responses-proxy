"""Windows native supervision lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import windows
from openai_responses_proxy.service import runtime as service_runtime
from tests.lifecycle.fixtures import platform_context
from tests.lifecycle.supervision.fixtures import completed as _completed
from tests.lifecycle.supervision.fixtures import temporary_context as _temporary_context

ROOT = Path(__file__).resolve().parents[3]


class TestWindowsLifecycle:
    def test_task_executes_the_installed_binary_in_private_watchdog_mode(self):
        ctx = platform_context(windows=True)
        rendered = windows.render_task_xml(ctx)
        decoded = rendered.decode("utf-16")
        assert f"<Command>{ctx.executable}</Command>" in decoded
        assert "<Arguments>--internal-watchdog</Arguments>" in decoded
        assert f"<WorkingDirectory>{ctx.payload_dir}</WorkingDirectory>" in decoded
        assert "python" not in decoded.lower()
        assert ".py" not in decoded

    def test_task_xml_bytes_use_the_declared_utf16_encoding(self, *, mocker):
        ctx = platform_context(windows=True)
        rendered = windows.render_task_xml(ctx)

        assert rendered.startswith((b"\xff\xfe", b"\xfe\xff"))
        assert "encoding='utf-16'" in rendered.decode("utf-16").splitlines()[0]
        assert windows.ET.fromstring(rendered).tag == f"{{{windows._TASK_NAMESPACE}}}Task"

        imported = []

        def run(arguments, **_kwargs):
            if arguments[:2] == ["schtasks", "/create"]:
                imported.append(Path(arguments[-1]).read_bytes())
            if arguments[-1:] == ["/xml"]:
                return _completed(stdout=rendered.decode("utf-16"))
            return _completed()

        mocker.patch.object(windows.subprocess, "run", side_effect=run)
        mocker.patch.object(windows, "_wait_for_watchdog", return_value=mocker.sentinel.watchdog)
        windows.install(ctx)

        assert imported == [rendered]

    def test_configured_executable_reads_only_the_registered_task(self, *, mocker):
        ctx = platform_context(windows=True)
        for completed, expected in (
            (_completed(returncode=1), None),
            (
                _completed(stdout=windows.render_task_xml(ctx).decode("utf-16")),
                ctx.executable,
            ),
            (_completed(stdout="not xml"), None),
            (
                _completed(
                    stdout=windows.render_task_xml(ctx)
                    .decode("utf-16")
                    .replace(f"<Command>{ctx.executable}</Command>", "")
                ),
                None,
            ),
        ):
            invoked = mocker.patch.object(windows.subprocess, "run", return_value=completed)
            assert windows.configured_executable(ctx) == expected
            assert invoked.call_args.args[0] == [
                "schtasks",
                "/query",
                "/tn",
                ctx.service_id,
                "/xml",
            ]

    def test_install_success_and_failure_messages(self, *, mocker):
        with _temporary_context("install_dir", windows=True) as ctx:
            task = windows.render_task_xml(ctx).decode("utf-16")
            invoked = mocker.patch.object(
                windows.subprocess,
                "run",
                side_effect=[
                    _completed(returncode=1),
                    _completed(),
                    _completed(),
                    _completed(),
                    _completed(stdout=task),
                ],
            )
            mocker.patch.object(
                windows, "_wait_for_watchdog", return_value=mocker.sentinel.watchdog
            )
            windows.install(ctx)
            assert invoked.call_args_list[3].args[0] == [
                "schtasks",
                "/run",
                "/tn",
                ctx.service_id,
            ]
            imported = Path(invoked.call_args_list[2].args[0][-1])
            assert not imported.exists()
            for completed, error in (
                (
                    _completed(returncode=1, stderr="denied at C:/Users/private/secret"),
                    "schtasks create failed",
                ),
                (
                    _completed(returncode=1, stdout="failed at C:/Users/private/secret"),
                    "schtasks create failed",
                ),
            ):
                mocker.patch.object(
                    windows.subprocess,
                    "run",
                    side_effect=[_completed(returncode=1), _completed(), completed],
                )
                with pytest.raises(errors.InstallError, match=error) as raised:
                    windows.install(ctx)
                assert "C:/Users/private/secret" not in str(raised.value)

    def test_install_replaces_only_a_proved_predecessor_generation(self, *, mocker) -> None:
        ctx = platform_context(windows=True)
        previous_executable = "C:/previous/openai-responses-proxy.exe"
        predecessor = windows.process.OwnedProcess(41, previous_executable, 1.0)
        mocker.patch.object(windows, "configured_executable", return_value=previous_executable)
        mocker.patch.object(windows.process, "pids_naming_executable", return_value=[41])
        capture = mocker.patch.object(
            windows.process,
            "capture_executable",
            return_value=None,
        )

        with pytest.raises(errors.InstallError, match="process identity is unproved"):
            windows.install(ctx)

        capture.assert_called_once_with(
            41,
            previous_executable,
            roles={service_runtime.WATCHDOG_MODE},
        )

        mocker.patch.object(windows.process, "capture_executable", return_value=predecessor)
        mocker.patch.object(windows.process, "terminate_owned_process", return_value=False)
        run = mocker.patch.object(
            windows.subprocess,
            "run",
            side_effect=[_completed(), _completed()],
        )

        with pytest.raises(errors.InstallError, match="predecessor watchdog 41 exit is unproved"):
            windows.install(ctx)

        run.assert_not_called()

    @pytest.mark.parametrize(
        ("started", "configured", "successor", "message"),
        [
            (
                _completed(returncode=1, stderr="denied at C:/Users/private/secret"),
                "expected",
                object(),
                "run failed",
            ),
            (_completed(), "other", object(), "task executable is unproved"),
            (
                _completed(),
                "expected",
                None,
                "successor watchdog process identity is unproved",
            ),
        ],
    )
    def test_install_requires_started_task_and_exact_successor(
        self, *, started, configured, successor, message, mocker
    ) -> None:
        ctx = platform_context(windows=True)
        configured = ctx.executable if configured == "expected" else configured
        mocker.patch.object(windows, "configured_executable", side_effect=[None, configured])
        mocker.patch.object(windows, "_wait_for_watchdog", return_value=successor)
        mocker.patch.object(
            windows.subprocess,
            "run",
            side_effect=[_completed(), _completed(), started],
        )

        with pytest.raises(errors.InstallError, match=message) as raised:
            windows.install(ctx)
        assert "C:/Users/private/secret" not in str(raised.value)

    def test_watchdog_wait_is_bounded_and_requires_one_live_identity(self, *, mocker) -> None:
        ctx = platform_context(windows=True)
        mocker.patch.object(windows, "_running_watchdog_pids", return_value=[])
        mocker.patch.object(windows.time, "monotonic", side_effect=[0.0, 1.0])
        sleep = mocker.patch.object(windows.time, "sleep")

        assert windows._wait_for_watchdog(ctx, timeout_seconds=0.5) is None
        sleep.assert_not_called()

        candidate = windows.process.OwnedProcess(41, ctx.executable, 1.0)
        mocker.patch.object(windows, "_running_watchdog_pids", return_value=[41])
        mocker.patch.object(windows.process, "capture_executable", return_value=candidate)
        mocker.patch.object(windows.process, "owned_process_alive", return_value=True)
        mocker.patch.object(windows.time, "monotonic", return_value=0.0)

        assert windows._wait_for_watchdog(ctx) == candidate

    def test_task_xml_serialization_preserves_special_characters(self, *, mocker):
        ctx = platform_context(windows=True)
        ctx.executable = f"{ctx.executable} & native"
        mocker.patch.object(windows, "_current_user", return_value="ACME\\A&B")

        root = windows.ET.fromstring(windows.render_task_xml(ctx))
        namespace = {"task": windows._TASK_NAMESPACE}

        assert root.findtext(".//task:Command", namespaces=namespace) == ctx.executable
        assert root.findtext(".//task:UserId", namespaces=namespace) == "ACME\\A&B"

    def test_current_user_and_status(self, *, mocker):
        for env, expected in (
            ({"USERNAME": "tester"}, "tester"),
            ({"USERNAME": "tester", "USERDOMAIN": "ACME"}, r"ACME\tester"),
        ):
            mocker.patch.dict(windows.os.environ, env, clear=True)
            assert windows._current_user() == expected
        for returncode, stdout, expected in (
            (1, "", "absent"),
            (0, "Ready", "installed"),
            (0, "Status: Running", "running"),
        ):
            mocker.patch.object(
                windows.subprocess,
                "run",
                return_value=_completed(returncode=returncode, stdout=stdout),
            )
            assert windows.status(platform_context(windows=True)) == expected

    def test_pid_discovery_and_uninstall(self, *, mocker):
        ctx = platform_context(windows=True)
        inventory = mocker.patch.object(
            windows.process,
            "pids_naming_executable",
            return_value=[12, 15, 18],
        )
        assert windows._running_watchdog_pids(ctx) == [12, 15, 18]
        inventory.assert_called_once_with(ctx.executable, roles={service_runtime.WATCHDOG_MODE})
        invoked = mocker.patch.object(
            windows.subprocess,
            "run",
            side_effect=[_completed(), _completed(returncode=1)],
        )
        mocker.patch.object(windows, "_running_watchdog_pids", return_value=[4242])
        terminate = mocker.patch.object(windows.process, "terminate_executable", return_value=False)

        with pytest.raises(errors.InstallError, match="verified watchdog 4242 exit is unproved"):
            windows.uninstall(ctx)
        assert invoked.call_args_list[0].args[0][:2] == ["schtasks", "/delete"]
        terminate.assert_called_once_with(
            4242,
            ctx.executable,
            roles={service_runtime.WATCHDOG_MODE},
        )
        mocker.patch.object(
            windows.subprocess,
            "run",
            side_effect=[_completed(), _completed(returncode=1)],
        )
        mocker.patch.object(windows, "_running_watchdog_pids", side_effect=[[4242], []])
        terminate = mocker.patch.object(windows.process, "terminate_executable", return_value=True)
        windows.uninstall(ctx)
        terminate.assert_called_once_with(
            4242,
            ctx.executable,
            roles={service_runtime.WATCHDOG_MODE},
        )

    def test_uninstall_requires_task_deletion_and_absence_proof(self, *, mocker):
        ctx = platform_context(windows=True)
        for results, message in (
            (
                [
                    _completed(returncode=1, stderr="denied at C:/Users/private/secret"),
                    _completed(),
                ],
                "delete failed",
            ),
            ([_completed(), _completed(stdout="Ready")], "remains registered"),
        ):
            mocker.patch.object(windows.subprocess, "run", side_effect=results)
            inventory = mocker.patch.object(windows, "_running_watchdog_pids")
            with pytest.raises(errors.InstallError, match=message) as raised:
                windows.uninstall(ctx)
            assert "C:/Users/private/secret" not in str(raised.value)
            inventory.assert_not_called()

    def test_uninstall_refuses_watchdog_residue_when_task_is_absent(self, *, mocker) -> None:
        ctx = platform_context(windows=True)
        mocker.patch.object(
            windows.subprocess,
            "run",
            side_effect=[_completed(returncode=1), _completed(returncode=1)],
        )
        mocker.patch.object(windows, "_running_watchdog_pids", side_effect=[[], [23]])
        with pytest.raises(errors.InstallError, match=r"watchdogs remain: \[23\]"):
            windows.uninstall(ctx)
