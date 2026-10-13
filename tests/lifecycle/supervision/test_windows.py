"""Windows native supervision lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import windows
from openai_responses_proxy.serving import roles as serving_roles
from tests.lifecycle.fixtures import platform_context
from tests.lifecycle.supervision.fixtures import completed as _completed
from tests.lifecycle.supervision.fixtures import temporary_context as _temporary_context

ROOT = Path(__file__).resolve().parents[3]


class TestWindowsLifecycle:
    @pytest.mark.parametrize("code", [0x80070002, -2147024894, 0x80070003, -2147024893])
    def test_exact_missing_task_hresult_proves_absence(self, code, *, mocker):
        ctx = platform_context(windows=True)
        run = mocker.patch.object(
            windows.subprocess, "run", return_value=_completed(returncode=code)
        )
        assert windows.configured_executable(ctx) is None
        assert windows.status(ctx) == "absent"
        assert all("/hresult" in call.args[0] for call in run.call_args_list)
        assert all(
            call.kwargs["stdin"] == windows.subprocess.DEVNULL for call in run.call_args_list
        )
        assert all(call.kwargs["timeout"] == 5.0 for call in run.call_args_list)

    @pytest.mark.parametrize(
        "code", [1, 0x80070005, -2147024891, 0x800706BA, 0x80070035, 0x80070057, 0x8007007B]
    )
    @pytest.mark.parametrize("operation", ["configured_executable", "status"])
    def test_failed_task_query_cannot_prove_absence(self, code, operation, *, mocker):
        mocker.patch.object(
            windows.subprocess,
            "run",
            return_value=_completed(returncode=code, stderr="private native output"),
        )
        with pytest.raises(errors.InstallError, match="task state is unproved") as raised:
            getattr(windows, operation)(platform_context(windows=True))
        assert "private native output" not in str(raised.value)

    @pytest.mark.parametrize("operation", ["configured_executable", "status"])
    @pytest.mark.parametrize("malformed", ["xml", "action"])
    def test_successful_query_requires_an_owned_task_projection(
        self, operation, malformed, *, mocker
    ):
        ctx = platform_context(windows=True)
        xml = windows.render_task_xml(ctx).decode("utf-16")
        if malformed == "xml":
            xml = "not xml"
        else:
            xml = xml.replace(f"<Command>{ctx.executable}</Command>", "")
        mocker.patch.object(windows.subprocess, "run", return_value=_completed(stdout=xml))
        with pytest.raises(errors.InstallError, match=r"task.*unproved"):
            getattr(windows, operation)(ctx)

    @pytest.mark.parametrize("live", [False, True])
    def test_task_status_uses_owned_processes_not_localized_output(self, live, *, mocker):
        ctx = platform_context(windows=True)
        xml = windows.render_task_xml(ctx).decode("utf-16")
        run = mocker.patch.object(windows.subprocess, "run", return_value=_completed(stdout=xml))
        inventory = mocker.patch.object(
            windows.process, "pids_naming_executable", return_value=[41] if live else []
        )
        assert windows.status(ctx) == ("running" if live else "installed")
        assert "/xml" in run.call_args.args[0]
        inventory.assert_called_once_with(ctx.executable, roles={serving_roles.WATCHDOG_MODE})

    @pytest.mark.parametrize(
        "failure",
        [
            OSError("private native error"),
            UnicodeError("unreadable native output"),
            windows.subprocess.TimeoutExpired("schtasks", 5.0),
        ],
    )
    def test_task_query_failure_preserves_unknown_state(self, failure, *, mocker):
        mocker.patch.object(windows.subprocess, "run", side_effect=failure)
        with pytest.raises(errors.InstallError, match="task state is unproved"):
            windows.status(platform_context(windows=True))

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
            if arguments[:2] == ["schtasks", "/query"]:
                return _completed(stdout=rendered.decode("utf-16"))
            return _completed()

        mocker.patch.object(windows.subprocess, "run", side_effect=run)
        mocker.patch.object(windows, "_wait_for_watchdog", return_value=mocker.sentinel.watchdog)
        windows.install(ctx)

        assert imported == [rendered]

    def test_configured_executable_reads_only_the_registered_task(self, *, mocker):
        ctx = platform_context(windows=True)
        for completed, expected in (
            (_completed(returncode=0x80070002), None),
            (
                _completed(stdout=windows.render_task_xml(ctx).decode("utf-16")),
                ctx.executable,
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
                "/hresult",
            ]

    def test_projection_compares_only_the_registered_task_action(self, *, mocker):
        ctx = platform_context(windows=True)
        task = windows.render_task_xml(ctx).decode("utf-16")
        exported = task.replace(
            "<Description>", "<Author>Task Scheduler normalization</Author><Description>"
        )
        moved = task.replace(
            f"<Command>{ctx.executable}</Command>",
            "<Command>C:/elsewhere/openai-responses-proxy.exe</Command>",
        )
        for completed, expected in (
            (_completed(returncode=0x80070002), False),
            (_completed(stdout=task), True),
            (_completed(stdout=exported), True),
            (_completed(stdout=moved), False),
        ):
            mocker.patch.object(windows.subprocess, "run", return_value=completed)
            assert windows.projection_is_current(ctx) is expected

    def test_install_success_and_failure_messages(self, *, mocker):
        with _temporary_context("install_dir", windows=True) as ctx:
            task = windows.render_task_xml(ctx).decode("utf-16")
            invoked = mocker.patch.object(
                windows.subprocess,
                "run",
                side_effect=[
                    _completed(returncode=0x80070002),
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
                    side_effect=[_completed(returncode=0x80070002), _completed(), completed],
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
            roles={serving_roles.WATCHDOG_MODE},
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

    def test_current_user(self, *, mocker):
        for env, expected in (
            ({"USERNAME": "tester"}, "tester"),
            ({"USERNAME": "tester", "USERDOMAIN": "ACME"}, r"ACME\tester"),
        ):
            mocker.patch.dict(windows.os.environ, env, clear=True)
            assert windows._current_user() == expected

    def test_pid_discovery_and_uninstall(self, *, mocker):
        ctx = platform_context(windows=True)
        inventory = mocker.patch.object(
            windows.process,
            "pids_naming_executable",
            return_value=[12, 15, 18],
        )
        assert windows._running_watchdog_pids(ctx) == [12, 15, 18]
        inventory.assert_called_once_with(ctx.executable, roles={serving_roles.WATCHDOG_MODE})
        invoked = mocker.patch.object(windows.subprocess, "run", return_value=_completed())
        mocker.patch.object(windows, "status", return_value="absent")
        mocker.patch.object(windows, "_running_watchdog_pids", return_value=[4242])
        terminate = mocker.patch.object(windows.process, "terminate_executable", return_value=False)

        with pytest.raises(errors.InstallError, match="verified watchdog 4242 exit is unproved"):
            windows.uninstall(ctx)
        assert invoked.call_args_list[0].args[0][:2] == ["schtasks", "/delete"]
        terminate.assert_called_once_with(
            4242,
            ctx.executable,
            roles={serving_roles.WATCHDOG_MODE},
        )
        mocker.patch.object(windows, "_running_watchdog_pids", side_effect=[[4242], []])
        terminate = mocker.patch.object(windows.process, "terminate_executable", return_value=True)
        windows.uninstall(ctx)
        terminate.assert_called_once_with(
            4242,
            ctx.executable,
            roles={serving_roles.WATCHDOG_MODE},
        )

    def test_uninstall_requires_task_deletion_and_absence_proof(self, *, mocker):
        ctx = platform_context(windows=True)
        for deleted, message in (
            (
                _completed(returncode=1, stderr="denied at C:/Users/private/secret"),
                "delete failed",
            ),
            (_completed(), "remains registered"),
        ):
            mocker.patch.object(windows.subprocess, "run", return_value=deleted)
            mocker.patch.object(windows, "status", return_value="installed")
            inventory = mocker.patch.object(windows, "_running_watchdog_pids")
            with pytest.raises(errors.InstallError, match=message) as raised:
                windows.uninstall(ctx)
            assert "C:/Users/private/secret" not in str(raised.value)
            inventory.assert_not_called()

    def test_uninstall_refuses_watchdog_residue_when_task_is_absent(self, *, mocker) -> None:
        ctx = platform_context(windows=True)
        mocker.patch.object(windows.subprocess, "run", return_value=_completed(returncode=1))
        mocker.patch.object(windows, "status", return_value="absent")
        mocker.patch.object(windows, "_running_watchdog_pids", side_effect=[[], [23]])
        with pytest.raises(errors.InstallError, match=r"watchdogs remain: \[23\]"):
            windows.uninstall(ctx)
