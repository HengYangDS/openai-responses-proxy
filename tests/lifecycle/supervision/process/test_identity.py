"""Exact native process and command-line identity observations."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.fixtures import platform_context


class TestProcessIdentity:
    def test_bounded_capture_waits_for_the_exact_native_role(self, *, mocker) -> None:
        expected = process.OwnedProcess(73, "/installed/openai-responses-proxy", 42.0)
        capture = mocker.patch.object(
            process,
            "capture_executable",
            side_effect=[None, None, expected],
        )
        mocker.patch.object(process.time, "sleep")

        assert (
            process.wait_for_executable(
                73,
                "/installed/openai-responses-proxy",
                roles={"--internal-watchdog"},
                timeout_seconds=1.0,
            )
            == expected
        )
        assert capture.call_count == 3

    def test_psutil_process_inventory_and_listener_discovery_are_host_command_free(self, *, mocker):
        first = mocker.Mock(pid=7)
        first.cmdline.return_value = ["python", "/installed/proxy.py"]
        first.info = {
            "net_connections": [
                mocker.Mock(status=process.psutil.CONN_LISTEN, laddr=mocker.Mock(port=8791))
            ]
        }
        second = mocker.Mock(pid=8)
        second.cmdline.side_effect = process.psutil.AccessDenied(8)
        second.info = {"net_connections": []}
        process_iter = mocker.patch.object(
            process.psutil,
            "process_iter",
            side_effect=([first, second], [first, second]),
        )

        assert process._process_inventory() == [(7, ["python", "/installed/proxy.py"])]
        assert process.listener_pids(8791) == [7]
        assert process_iter.call_args_list[1].args == (["pid", "net_connections"],)

    def test_process_discovery_fails_closed(self, *, mocker):
        mocker.patch.object(
            process.psutil,
            "process_iter",
            side_effect=process.psutil.AccessDenied(),
        )
        assert process.listener_pids(8791) == []
        mocker.patch.object(
            process.psutil,
            "Process",
            side_effect=process.psutil.NoSuchProcess(123),
        )
        assert process.process_argv(123) == []
        assert process.process_command(123) == ""

    def test_process_inventory_and_path_discovery_fail_closed(self, *, mocker):
        mocker.patch.object(
            process.psutil,
            "process_iter",
            side_effect=process.psutil.AccessDenied(),
        )
        assert process._process_inventory() == []
        mocker.patch.object(
            process,
            "_process_inventory",
            return_value=[
                (7, ["python", "/installed/watchdog.py.backup"]),
                (8, ["python", "/installed/watchdog.py"]),
                (9, ["python", "-c", "print", "/installed/watchdog.py"]),
            ],
        )
        mocker.patch.object(
            process,
            "process_argv",
            side_effect=[
                ["python", "/installed/watchdog.py.backup"],
                ["python", "/installed/watchdog.py"],
                ["python", "-c", "print", "/installed/watchdog.py"],
            ],
        )
        assert process.pids_naming_path("/installed/watchdog.py") == [8]

    def test_argv_denial_reported_as_system_error_is_unreadable(self, *, mocker):
        readable = mocker.Mock(pid=7)
        readable.cmdline.return_value = ["python", "/installed/proxy.py"]
        denied = mocker.Mock(pid=8)
        denial = SystemError(
            "<built-in function proc_cmdline> returned a result with an exception set"
        )
        denial.__cause__ = denial.__context__ = PermissionError(
            errno.EACCES,
            "force permission denied (originated from sysctl(KERN_PROCARGS2) -> errno 0)",
        )
        denied.cmdline.side_effect = denial
        mocker.patch.object(process.psutil, "process_iter", return_value=[readable, denied])
        mocker.patch.object(process.psutil, "Process", return_value=denied)

        assert process._process_inventory() == [(7, ["python", "/installed/proxy.py"])]
        assert process.process_argv(8) == []
        assert process.capture_executable(8, "/installed/proxy.py") is None

        unrelated = mocker.Mock(pid=9)
        unrelated.cmdline.side_effect = SystemError("unrelated interpreter failure")
        mocker.patch.object(process.psutil, "Process", return_value=unrelated)
        with pytest.raises(SystemError, match="unrelated interpreter failure"):
            process.process_argv(9)

    def test_native_and_foreign_script_identity_are_distinct(self, subtests, *, mocker):
        ctx = platform_context()
        executable = os.path.abspath(ctx.executable)
        listener = "--internal-listener"
        handoff_child = "--internal-handoff-child"
        cases = (
            ([executable, listener], True),
            ([executable, handoff_child], True),
            ([executable, "--internal-watchdog"], False),
            ([f"{executable}.backup", listener], False),
            ([executable], False),
            ([], False),
        )
        for argv, expected in cases:
            with subtests.test(argv=argv):
                assert (
                    process.argv_names_executable(argv, executable, roles={listener, handoff_child})
                    == expected
                )
        mocker.patch.object(process, "listener_pids", return_value=[7, 8])
        mocker.patch.object(
            process,
            "process_argv",
            side_effect=[[executable, "--internal-watchdog"], [executable, listener]],
        )
        assert process.verified_proxy_listener_pids(ctx) == [8]

        foreign = str(Path("/") / "fixture-home" / "foreign" / "listener.py")
        mocker.patch.object(process, "listener_pids", return_value=[7, 8])
        mocker.patch.object(
            process,
            "process_argv",
            side_effect=[["python", foreign], [executable, listener]],
        )
        assert process.verified_listener_pids(ctx.port, foreign) == [7]

    def test_native_identity_survives_a_retired_launcher_argv(self, *, mocker):
        expected = os.path.abspath("/installed/openai-responses-proxy")
        candidate = mocker.Mock()
        candidate.exe.return_value = expected
        candidate.cmdline.return_value = [
            "/installed/.retired-launcher",
            "--internal-handoff-child",
        ]
        mocker.patch.object(process.psutil, "Process", return_value=candidate)

        assert process.pid_names_executable(
            17,
            expected,
            roles={"--internal-handoff-child"},
        )

    def test_identity_helpers_cover_empty_roles_and_python_suffixes(self, subtests, *, mocker):
        executable = os.path.abspath("/installed/openai-responses-proxy")
        for argv, roles, expected in (
            ([], None, False),
            ([executable], None, True),
            ([executable, "--watchdog"], {"--listener"}, False),
        ):
            with subtests.test(argv=argv, roles=roles):
                assert process.argv_names_executable(argv, executable, roles=roles) is expected

        for argv, expected in (
            (["python3.12", "/installed/proxy.py"], True),
            (["pythonw.exe", "/installed/proxy.py"], True),
            (["ruby", "/installed/proxy.py"], False),
            (["python"], False),
        ):
            with subtests.test(argv=argv):
                mocker.patch.object(process, "process_argv", return_value=argv)
                assert process.pid_names_path(17, "/installed/proxy.py") is expected
                mocker.stopall()

        mocker.patch.object(process, "_process_inventory", return_value=[(17, [executable])])
        mocker.patch.object(process, "pid_names_executable", return_value=True)
        assert process.pids_naming_executable(executable) == [17]

    def test_process_command_quotes_native_argv(self, *, mocker):
        mocker.patch.object(
            process,
            "process_argv",
            return_value=["/usr/bin/python3", "/installed/proxy with spaces.py"],
        )
        assert process.process_command(17) == ("/usr/bin/python3 '/installed/proxy with spaces.py'")

    @pytest.mark.skipif(sys.platform != "darwin", reason="real Darwin argv integration")
    def test_real_process_argv_preserves_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="responses proxy argv ") as directory:
            fixture = Path(directory, "listener entrypoint.py")
            fixture.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            child = subprocess.Popen([sys.executable, str(fixture)])
            try:
                # A framework interpreter's launcher re-executes its app bundle, so the
                # first argument may name either; the path with spaces must survive intact.
                argv = process.process_argv(child.pid)
                assert argv[1:] == [str(fixture)]
                assert Path(argv[0]).is_absolute()
                assert Path(argv[0]).name.lower().startswith("python")
                assert process.pid_names_path(child.pid, str(fixture))
                assert child.pid in process.pids_naming_path(str(fixture))
            finally:
                child.terminate()
                child.wait(timeout=5)
