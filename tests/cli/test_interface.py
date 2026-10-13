"""Black-box contracts for the installed proxy product interface."""

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import os
import runpy
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.cli import application
from openai_responses_proxy.runtime.process_environment import native_process_environment
from openai_responses_proxy.serving import roles as serving_roles

ROOT = Path(__file__).resolve().parents[2]


class ProductInterfaceContracts:
    """Keep one executable and one bounded public command grammar."""

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = application.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_help_exposes_exactly_the_declared_public_commands(self) -> None:
        code, stdout, stderr = self.invoke("--help")
        assert code == 0
        assert stderr == ""
        assert all(command in stdout for command in application.PUBLIC_COMMANDS)

    def test_empty_and_subcommand_help_are_successful(self) -> None:
        for arguments in (
            (),
            *((command, "--help") for command in application.PUBLIC_COMMANDS),
        ):
            code, stdout, stderr = self.invoke(*arguments)
            assert code == 0
            assert "Usage: openai-responses-proxy" in stdout
            assert stderr == ""

    def test_top_level_version_flag_is_the_only_version_surface(self) -> None:
        code, stdout, stderr = self.invoke("--version")

        assert code == 0
        assert stdout.strip() == application._release_version()
        assert stderr == ""

    def test_every_public_command_offers_the_same_machine_output_switch(self) -> None:
        for command in application.PUBLIC_COMMANDS:
            code, stdout, stderr = self.invoke(command, "--help")
            assert code == 0
            assert "--json" in stdout
            assert "stable JSON" in stdout
            json_line = next(line for line in stdout.splitlines() if "--json" in line)
            assert "[default: False]" not in json_line
            assert stderr == ""

    def test_every_public_command_has_bounded_invalid_input(self) -> None:
        cases = (
            ("install",),
            ("status", "--port", "invalid"),
            ("doctor", "--unknown"),
            ("recover", "--port", "invalid"),
            ("reload", "--timeout-seconds", "invalid"),
            ("rollback",),
            ("uninstall", "--port", "invalid"),
        )
        for arguments in cases:
            code, stdout, stderr = self.invoke(*arguments)
            assert code == 2
            assert stdout == ""
            assert stderr
            assert "Traceback" not in stderr
            assert "Warning" not in stderr

    def test_every_public_command_names_one_next_action_in_json_and_text(self, *, mocker) -> None:
        """Each success carries one next action that both renderers project identically."""
        results = {
            "install": {"state": "unchanged", "release": "2.0.48"},
            "status": {
                "state": "not_installed",
                "detail": "not installed",
                "release": None,
                "payload_integrity": {"ok": False, "detail": "not installed"},
                "command": {"state": "absent", "kind": None, "path": "/commands/proxy"},
                "service": "absent",
                "listener_pids": [],
                "runtime": None,
                "payload_transaction": None,
            },
            "doctor": {
                "ok": True,
                "state": "running",
                "checks": {"payload": {"status": "passed", "detail": "verified"}},
                "next": None,
            },
            "rollback": {"state": "unavailable", "detail": "no verified predecessor"},
            "recover": {"state": "closed", "version": "2.0.48", "transaction_id": "tx"},
            "reload": {"state": "reloaded", "old_pid": 41, "new_pid": 42},
            "uninstall": {"state": "uninstalled", "stopped": 1, "command_removed": True},
        }
        expected_next = {
            "install": "openai-responses-proxy status",
            "status": "openai-responses-proxy install --help",
            "doctor": None,
            "rollback": "openai-responses-proxy status",
            "recover": "openai-responses-proxy status",
            "reload": "openai-responses-proxy status",
            "uninstall": None,
        }
        arguments = {
            "install": (
                "--asset",
                "release.tar.gz",
                "--trust-anchor",
                "allowed-signers",
            ),
            "status": (),
            "doctor": (),
            "rollback": ("--to-release", "2.0.47"),
            "recover": (),
            "reload": (),
            "uninstall": (),
        }
        dispatch = mocker.patch.object(application, "dispatch")
        for command in application.PUBLIC_COMMANDS:
            dispatch.return_value = results[command]
            code, stdout, stderr = self.invoke(command, *arguments[command], "--json")
            assert code == 0
            assert json.loads(stdout) == {**results[command], "next": expected_next[command]}
            assert stderr == ""
            code, human, stderr = self.invoke(command, *arguments[command])
            lines = human.splitlines()
            shown = lines[lines.index("Next") + 1].strip() if "Next" in lines else None
            assert (code, shown, stderr) == (0, expected_next[command], "")

    def test_parse_failures_are_bounded_in_text_and_json(self) -> None:
        cases = (
            (("status", "--port", "not-an-integer"), False),
            (("--json",), True),
        )
        for arguments, as_json in cases:
            code, stdout, stderr = self.invoke(*arguments)
            assert code == 2
            assert stdout == ""
            if as_json:
                error = json.loads(stderr)["error"]
                assert error["message"]
                assert error["next"] == "openai-responses-proxy --help"
            else:
                assert "Action required" in stderr
                assert "Problem" in stderr
                assert "Next" in stderr
            assert "Traceback" not in stderr
            assert "Warning" not in stderr

    def test_parse_failures_point_to_the_exact_command_help(self) -> None:
        code, stdout, stderr = self.invoke("install")

        assert code == 2
        assert stdout == ""
        assert "openai-responses-proxy install --help" in stderr
        assert "openai-responses-proxy doctor" not in stderr

        code, stdout, stderr = self.invoke("unknown")

        assert code == 2
        assert stdout == ""
        assert "openai-responses-proxy --help" in stderr
        assert "openai-responses-proxy doctor" not in stderr

    def test_help_explains_lifecycle_inputs_without_boolean_noise(self) -> None:
        code, install_help, stderr = self.invoke("install", "--help")
        assert code == 0
        assert stderr == ""
        assert "Native release archive" in install_help
        assert "sibling manifest" in install_help
        assert "checksums, and signature required" in install_help
        assert "Trusted SSH allowed-signers file" in install_help
        assert "Loopback listener port" in install_help
        assert "Native lifecycle deadline" in install_help

        code, reload_help, stderr = self.invoke("reload", "--help")
        assert code == 0
        assert stderr == ""
        assert "Native lifecycle deadline" in reload_help
        assert "Installation deadline" not in reload_help

        code, uninstall_help, stderr = self.invoke("uninstall", "--help")
        assert code == 0
        assert stderr == ""
        assert "Also remove the verified installed payload" in uninstall_help
        assert "--no-purge" not in uninstall_help
        assert "[default: False]" not in uninstall_help

    def test_missing_install_inputs_are_clean_usage_errors(self, tmp_path: Path) -> None:
        asset = tmp_path / "missing-release.tar.gz"
        trust_anchor = tmp_path / "missing-allowed-signers"

        for arguments in (
            (
                "install",
                "--asset",
                str(asset),
                "--trust-anchor",
                str(trust_anchor),
            ),
            (
                "install",
                "--asset",
                str(asset),
                "--trust-anchor",
                str(trust_anchor),
                "--json",
            ),
        ):
            code, stdout, stderr = self.invoke(*arguments)

            assert code == 2
            assert stdout == ""
            assert "native release archive is unavailable" in stderr
            assert "[Errno" not in stderr
            assert "openai-responses-proxy install --help" in stderr
            assert "openai-responses-proxy doctor" not in stderr
            assert "Traceback" not in stderr
            assert "Warning" not in stderr

        asset = tmp_path / "openai-responses-proxy-2.0.48-macos-arm64.tar.gz"
        asset.write_bytes(b"not-admitted")
        code, stdout, stderr = self.invoke(
            "install",
            "--asset",
            str(asset),
            "--trust-anchor",
            str(trust_anchor),
            "--json",
        )

        assert code == 2
        assert stdout == ""
        assert "release trust anchor is unavailable" in stderr
        assert "[Errno" not in stderr
        assert "openai-responses-proxy install --help" in stderr

    def test_ports_and_deadlines_are_rejected_at_the_command_boundary(self) -> None:
        cases = (
            ("status", "--port", "0"),
            ("status", "--port", "65536"),
            ("reload", "--timeout-seconds", "0"),
            ("reload", "--timeout-seconds", "-1"),
        )
        for arguments in cases:
            code, stdout, stderr = self.invoke(*arguments)
            assert code == 2
            assert stdout == ""
            assert f"openai-responses-proxy {arguments[0]} --help" in stderr
            assert "openai-responses-proxy doctor" not in stderr
            assert "Traceback" not in stderr
            assert "Warning" not in stderr

    def test_module_boundary_delegates_exit_status(self, *, mocker) -> None:
        main = mocker.patch.object(application, "main", return_value=7)
        with pytest.raises(SystemExit, match="7"):
            runpy.run_module("openai_responses_proxy.cli.__main__", run_name="__main__")
        main.assert_called_once_with()

    def test_version_flag_uses_installed_distribution_metadata_without_a_checkout_file(
        self, *, mocker
    ) -> None:
        expected = importlib.metadata.version("openai-responses-proxy")
        mocker.patch.object(application, "_source_version", return_value=None)
        code, stdout, stderr = self.invoke("--version")
        assert code == 0
        assert stdout.strip() == expected
        assert stderr == ""

    def test_expected_errors_are_concise_and_machine_readable(self, *, mocker) -> None:
        mocker.patch.object(
            application,
            "dispatch",
            side_effect=errors.InstallError(
                "invalid value",
                next_command="openai-responses-proxy status",
            ),
        )
        code, stdout, stderr = self.invoke("doctor", "--json")
        assert code == 2
        assert stdout == ""
        payload = json.loads(stderr)
        assert payload["error"]["code"] == "lifecycle_error"
        assert payload["error"]["message"] == "invalid value"
        assert payload["error"]["next"] == "openai-responses-proxy status"
        assert "Traceback" not in stderr
        assert "Warning" not in stderr

    def test_unknown_command_is_a_clean_usage_error(self) -> None:
        code, stdout, stderr = self.invoke("unknown")
        assert code == 2
        assert stdout == ""
        assert "unknown command" in stderr
        assert "Traceback" not in stderr

    def test_human_error_has_problem_and_next_action_without_internal_leakage(
        self, *, mocker
    ) -> None:
        mocker.patch.object(
            application,
            "dispatch",
            side_effect=errors.InstallError("listener unavailable"),
        )

        code, stdout, stderr = self.invoke("status")

        assert code == 2
        assert stdout == ""
        assert "Problem" in stderr
        assert "listener unavailable" in stderr
        assert "Next" in stderr
        assert "openai-responses-proxy doctor" in stderr
        assert "python -m" not in stderr
        assert "Traceback" not in stderr

    def test_internal_assembly_failures_are_bounded_without_traceback(self, *, mocker) -> None:
        mocker.patch.object(
            application,
            "dispatch",
            side_effect=errors.ProductAssemblyError(
                "product installation is incomplete; reinstall the verified release"
            ),
        )

        code, stdout, stderr = self.invoke(
            "install",
            "--asset",
            "release.tar.gz",
            "--trust-anchor",
            "allowed-signers",
        )

        assert code == 2
        assert stdout == ""
        assert "product installation is incomplete" in stderr
        assert "reinstall the verified release" in stderr
        assert "openai_responses_proxy" not in stderr
        assert "Traceback" not in stderr
        assert "Warning" not in stderr

    @pytest.mark.parametrize("relative_roots", [False, True])
    def test_built_executable_reports_a_pristine_native_installation(
        self, tmp_path: Path, relative_roots: bool, *, monkeypatch
    ) -> None:
        """Require a real absent-installation report, not an accepted error exit."""
        executable = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        if executable is None:
            pytest.skip("native executable supplied by release session")
        monkeypatch.chdir(tmp_path)
        root = Path("sandbox") if relative_roots else tmp_path / "sandbox"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            result = subprocess.run(
                [executable, "status", "--json", "--port", str(reservation.getsockname()[1])],
                cwd=tmp_path,
                env=native_process_environment(
                    user_home=root / "home",
                    install_root=root / "payload",
                    state_root=root / "state",
                    command_search_path=tmp_path / "empty-path",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        report = json.loads(result.stdout)
        assert report["state"] == "not_installed"
        assert report["service"] == "absent"
        assert report["listener_pids"] == []
        assert report["runtime"] is None
        assert report["payload_transaction"] is None

    @pytest.mark.parametrize("argument", ["--version", serving_roles.PREWARM_MODE])
    def test_built_executable_runs_without_python_on_path(self, argument: str) -> None:
        executable = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        if executable is None:
            pytest.skip("native executable supplied by release session")
        with tempfile.TemporaryDirectory() as empty_path:
            root = Path(empty_path)
            result = subprocess.run(
                [executable, argument],
                cwd=root,
                env=native_process_environment(
                    user_home=root / "home",
                    install_root=root / "payload",
                    state_root=root / "state",
                    command_search_path=root,
                ),
                text=True,
                capture_output=True,
                check=False,
            )
        assert result.returncode == 0, result.stderr
        expected = application._release_version() if argument == "--version" else ""
        assert result.stdout.strip() == expected
        assert result.stderr == ""

    def test_built_executable_exercises_every_public_command_contract(self) -> None:
        executable = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        if executable is None:
            pytest.skip("native executable supplied by release session")
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            environment = native_process_environment(
                user_home=root / "home",
                install_root=root / "payload",
                state_root=root / "state",
                command_search_path=root / "empty-path",
            )
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = str(reservation.getsockname()[1])
                for command in application.PUBLIC_COMMANDS:
                    help_result = subprocess.run(
                        [executable, command, "--help"],
                        cwd=home,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    assert help_result.returncode == 0, help_result.stderr
                    assert "--json" in help_result.stdout
                    assert help_result.stderr == ""

                cases = {
                    "install": (2, ()),
                    "status": (0, ("--port", port)),
                    "doctor": (1, ("--port", port)),
                    "recover": (0, ("--port", port)),
                    "reload": (2, ("--port", port)),
                    "rollback": (0, ("--to-release", "0.0.0", "--port", port)),
                    "uninstall": (0, ("--port", port)),
                }
                for command, (expected_code, arguments) in cases.items():
                    result = subprocess.run(
                        [executable, command, *arguments, "--json"],
                        cwd=home,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    assert result.returncode == expected_code, result.stderr
                    payload = result.stdout or result.stderr
                    assert json.loads(payload)
                    assert "Traceback" not in result.stderr
                    assert "Warning" not in result.stderr

                for command, (expected_code, arguments) in cases.items():
                    result = subprocess.run(
                        [executable, command, *arguments],
                        cwd=home,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    assert result.returncode == expected_code, result.stderr
                    output = result.stdout or result.stderr
                    assert output.strip()
                    assert not output.lstrip().startswith("{")
                    assert "Traceback" not in result.stderr
                    assert "Warning" not in result.stderr

    def test_human_output_is_safe_for_the_default_windows_console_codec(self, *, mocker) -> None:
        mocker.patch.object(
            application,
            "dispatch",
            return_value={
                "state": "not_installed",
                "detail": "not installed",
                "command": {"state": "absent", "kind": None, "path": "C:\\proxy.cmd"},
                "listener_pids": [],
                "payload_integrity": {"ok": False, "detail": "not installed"},
                "release": None,
                "service": "absent",
                "runtime": None,
                "payload_transaction": None,
            },
        )

        code, stdout, stderr = self.invoke("status")

        assert code == 0
        assert stderr == ""
        stdout.encode("cp1252")
