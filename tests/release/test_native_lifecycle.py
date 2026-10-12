"""Signed native lifecycle acceptance against one isolated installation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from threading import Thread
from typing import cast

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import transaction as payload_transaction
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release import fixtures as release_fixtures
from tests.release.fixtures import cleanup_runtime
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import post_response
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import stop_native_supervision
from tests.service.handoff.fixtures import ScriptedUpstream
from tests.service.handoff.fixtures import free_port
from tools.release.artifact import signing

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestSignedNativeLifecycle:
    """Prove the public native lifecycle without touching the canonical service."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows native delete sharing")
    def test_windows_purge_waits_for_an_owned_native_file_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercise a real delete-denying native handle, not a mocked OS error."""
        import ctypes
        from ctypes import wintypes

        root = tmp_path / "payload"
        root.mkdir()
        target = root / "owned.dll"
        note = root / "operator-note.txt"
        content = b"owned native sharing fixture"
        target.write_bytes(content)
        note.write_bytes(b"preserve unknown content")
        windows_library: object = getattr(ctypes, "WinDLL", None)
        assert callable(windows_library), "native Windows library loading is unavailable"
        native = cast(Callable[..., ctypes.CDLL], windows_library)("kernel32", use_last_error=True)
        create_file = native.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        close_handle = native.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        # GENERIC_READ with FILE_SHARE_READ only: deletion is denied while open.
        handle = create_file(str(target), 0x80000000, 0x00000001, None, 3, 0, None)
        assert handle not in {None, ctypes.c_void_p(-1).value}
        locked = True
        try:
            original_mode = target.stat().st_mode
            original_unlink = Path.unlink
            delete_denied = Event()
            released = Event()
            close_succeeded = []
            native_denials = []

            def observe_unlink(path: Path, *args, **kwargs) -> None:
                try:
                    original_unlink(path, *args, **kwargs)
                except OSError as error:
                    if path == target:
                        native_denials.append(getattr(error, "winerror", None))
                        assert target.stat().st_mode == original_mode
                        delete_denied.set()
                    raise

            monkeypatch.setattr(Path, "unlink", observe_unlink)

            def release_lock() -> None:
                if not delete_denied.wait(timeout=2):
                    return
                time.sleep(0.1)
                close_succeeded.append(bool(close_handle(handle)))
                released.set()

            release = Thread(target=release_lock)
            release.start()
            try:
                remaining = payload_projection.purge_owned_files(
                    root, {target.name: hashlib.sha256(content).hexdigest()}
                )
                assert remaining == (note.name,)
            finally:
                release.join(timeout=2)
                assert not release.is_alive()
                locked = close_succeeded != [True]
                assert close_succeeded == [True]
                assert released.is_set()
            assert native_denials
            assert set(native_denials).issubset({5, 32})
            assert not target.exists()
            assert note.read_bytes() == b"preserve unknown content"
        finally:
            if locked:
                assert close_handle(handle)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows scheduled-task boundary")
    def test_missing_windows_task_has_native_absence_evidence(self, tmp_path: Path) -> None:
        """A missing task or task path is absent, not an unreadable registration."""
        home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        observed = subprocess.run(
            ["schtasks", "/query", "/tn", ctx.service_id, "/xml", "/hresult"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5.0,
        )
        result = observed.returncode & 0xFFFFFFFF
        assert result in {0x80070002, 0x80070003}, f"unexpected task HRESULT {result:#010x}"
        environment = native_process_environment(
            user_home=home, install_root=install, state_root=state
        )
        executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(
            strict=True
        )
        status = run_command(executable, environment, "status", "--port", str(port), "--json")
        assert status["state"] == "not_installed"
        assert status["service"] == "absent"
        assert native_service_projection(ctx)["status"] == "absent"
        assert not install.exists()
        assert not state.exists()

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux user-service boundary")
    def test_unavailable_user_bus_leaves_no_installation(self, tmp_path: Path) -> None:
        executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(
            strict=True
        )
        home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
        home.mkdir()
        runtime_directory = home / "run"
        runtime_directory.mkdir(mode=0o700)
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home, install_root=install, state_root=state
        )
        environment.update(
            XDG_RUNTIME_DIR=str(runtime_directory),
            DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime_directory / 'bus'}",
        )
        observation = subprocess.run(
            ["systemctl", "--user", "show", "--property=SystemState", "--value"],
            env=environment,
            capture_output=True,
            check=False,
            timeout=release_fixtures.COMMAND_TIMEOUT_SECONDS,
        )
        assert observation.returncode != 0
        key = tmp_path / "release-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text(encoding="ascii").strip()
        trust = f'{signing.PRINCIPAL} namespaces="{signing.NAMESPACE}" {public_key}'
        anchor = tmp_path / "allowed-signers"
        anchor.write_text(trust + "\n", encoding="ascii")
        asset = signed_asset(
            executable.parent,
            tmp_path / "assets",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
            upstream_url="http://127.0.0.1:1",
            key=key,
            trust=trust,
            preserve_manifest=True,
        )

        result = run_command(
            executable,
            environment,
            "install",
            "--asset",
            str(asset),
            "--trust-anchor",
            str(anchor),
            "--port",
            str(port),
            "--json",
            expected=2,
        )
        assert result == {
            "error": {
                "code": "native_service_unavailable",
                "message": (
                    "a reachable systemd user manager is required for Linux installation; "
                    "enable a systemd user session and retry installation"
                ),
                "next": "openai-responses-proxy install --help",
            }
        }
        observed = run_command(executable, environment, "status", "--port", str(port), "--json")
        assert observed["payload_transaction"] is None
        assert observed["runtime"] is None
        assert observed["listener_pids"] == []
        assert observed["command"] == {"kind": None, "path": ctx.command, "state": "absent"}
        assert not install.exists()
        assert not payload_state.journal_path(ctx).exists()
        assert not payload_state.transaction_root(ctx).exists()
        assert not any(path.is_file() or path.is_symlink() for path in home.rglob("*"))

    def test_signed_fresh_lifecycle_is_transactional(self, tmp_path: Path) -> None:
        executable_value = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        assert executable_value is not None, "native executable must be supplied by release session"
        executable = Path(executable_value).resolve(strict=True)
        bundle = executable.parent
        home, install, state = (
            tmp_path / "home",
            tmp_path / "payload",
            tmp_path / "state",
        )
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home,
            install_root=install,
            state_root=state,
        )
        assert ctx.service_id != runtime_context.SERVICE_ID
        isolated_before = native_service_projection(ctx)
        assert isolated_before == {
            "service_id": ctx.service_id,
            "status": "absent",
            "configured_executable": None,
            "processes": [],
        }
        canonical_ctx = runtime_context.create()
        canonical_service_before = native_service_projection(canonical_ctx)
        canonical_before = process.listener_pids(runtime_config.DEFAULT_PORT)

        key = tmp_path / "release-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text(encoding="ascii").strip()
        trust = f'{signing.PRINCIPAL} namespaces="{signing.NAMESPACE}" {public_key}'
        anchor = tmp_path / "allowed-signers"
        anchor.write_text(trust + "\n", encoding="ascii")

        upstream = ScriptedUpstream()
        upstream.start()
        upstream_url = upstream.base_url().replace("127.0.0.1", "localhost")
        current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        current_asset = signed_asset(
            bundle,
            tmp_path / "current-assets",
            version=current_version,
            upstream_url=upstream_url,
            key=key,
            trust=trust,
            preserve_manifest=True,
        )

        with ExitStack() as cleanups:
            cleanups.callback(upstream.close)
            cleanups.callback(cleanup_runtime, ctx)

            installed = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--upstream-base-url",
                upstream_url,
                "--json",
            )
            assert installed["state"] == "installed"
            status = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert status["release"] == current_version
            assert status["service"] == "running"
            installed_runtime = installed.get("runtime")
            assert isinstance(installed_runtime, dict)
            installed_pid = installed_runtime.get("pid")
            assert type(installed_pid) is int
            selected = generation.read(ctx)
            installed_bytes = payload_state.installed_path(ctx).read_bytes()
            supervisor = native_service_projection(ctx)
            repeated = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
            )
            assert repeated == {"state": "unchanged", "release": current_version}
            assert generation.read(ctx) == selected
            assert payload_state.installed_path(ctx).read_bytes() == installed_bytes
            assert native_service_projection(ctx) == supervisor
            assert process.listener_pids(port) == [installed_pid]
            assert payload_state.status(ctx) is None
            assert (
                run_command(executable, environment, "doctor", "--port", str(port), "--json")["ok"]
                is True
            )
            upstream.push((200, b'{"id":"ok","status":"completed"}'))
            assert post_response(port, route="upstream") == b'{"id":"ok","status":"completed"}'
            assert (
                run_command(executable, environment, "reload", "--port", str(port), "--json")[
                    "new_pid"
                ]
                != installed_pid
            )
            upstream.push((200, b'{"id":"after-reload","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-reload","status":"completed"}'
            )
            stop_native_supervision(ctx)
            stopped = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert stopped["state"] == "stopped"
            assert stopped["service"] == "installed"
            assert stopped["runtime"] is None
            diagnosed = run_command(
                executable, environment, "doctor", "--port", str(port), "--json", expected=1
            )
            assert diagnosed["state"] == "stopped"
            assert diagnosed["next"] == "openai-responses-proxy reload"
            started = run_command(executable, environment, "reload", "--port", str(port), "--json")
            assert started["state"] == "started"
            assert process.listener_pids(port) == [started["new_pid"]]
            restored = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert restored["state"] == "running"
            assert restored["service"] == "running"
            upstream.push((200, b'{"id":"after-start","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-start","status":"completed"}'
            )
            residue = install / "operator-note.txt"
            residue.write_text("preserve\n", encoding="utf-8")
            refused_purge = run_command(
                executable,
                environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
                expected=2,
            )
            assert refused_purge["error"] == {
                "code": "lifecycle_error",
                "message": "unknown install content remains: operator-note.txt",
                "next": "openai-responses-proxy doctor",
            }
            assert residue.read_text(encoding="utf-8") == "preserve\n"
            assert not payload_state.transaction_root(ctx).exists()
            pending_purge = payload_state.read_journal(ctx)
            assert pending_purge["state"] == "purged"
            refused_install = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
                expected=2,
            )
            assert refused_install["error"] == {
                "code": "lifecycle_error",
                "message": (
                    "installed payload root contains unverified content; "
                    "remove it explicitly before installing"
                ),
                "next": "openai-responses-proxy doctor",
            }
            assert not payload_state.transaction_root(ctx).exists()
            residue.unlink()
            recovered_purge = run_command(
                executable, environment, "recover", "--port", str(port), "--json"
            )
            assert recovered_purge == {
                "state": "purged",
                "transaction_id": pending_purge["transaction_id"],
                "version": current_version,
                "stopped": 0,
                "command_removed": False,
            }
            assert not install.exists()
            assert not payload_state.journal_path(ctx).exists()

            reinstalled = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
            )
            assert reinstalled["state"] == "installed"
            reinstalled_status = run_command(
                executable,
                environment,
                "status",
                "--port",
                str(port),
                "--json",
            )
            assert reinstalled_status["release"] == current_version
            assert reinstalled_status["service"] == "running"
            run_command(
                executable,
                environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
            )
            assert not install.exists()

            payload_transaction.begin_transaction(
                ctx,
                artifact.admit(current_asset, trust_anchor=anchor),
            )
            transaction_id = payload_state.read_journal(ctx)["transaction_id"]
            recovered = run_command(
                executable, environment, "recover", "--port", str(port), "--json"
            )
            assert recovered == {
                "state": "closed",
                "transaction_id": transaction_id,
                "version": current_version,
            }
            assert not payload_state.transaction_root(ctx).exists()
            assert not payload_state.journal_path(ctx).exists()
        assert native_service_projection(ctx) == isolated_before
        assert native_service_projection(canonical_ctx) == canonical_service_before
        assert process.listener_pids(runtime_config.DEFAULT_PORT) == canonical_before

    @pytest.mark.parametrize("interruption", [AssertionError, TimeoutError, KeyboardInterrupt])
    def test_native_teardown_survives_test_control_flow_failures(
        self,
        tmp_path: Path,
        interruption: type[BaseException],
    ) -> None:
        """Leave no native service behind when a test aborts after installation."""
        executable_value = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        assert executable_value is not None, "native executable must be supplied by release session"
        executable = Path(executable_value).resolve(strict=True)
        home, install, state = (
            tmp_path / "home",
            tmp_path / "payload",
            tmp_path / "state",
        )
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home,
            install_root=install,
            state_root=state,
        )
        isolated_before = native_service_projection(ctx)
        canonical_ctx = runtime_context.create()
        canonical_before = native_service_projection(canonical_ctx)

        key = tmp_path / "release-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text(encoding="ascii").strip()
        trust = f'{signing.PRINCIPAL} namespaces="{signing.NAMESPACE}" {public_key}'
        anchor = tmp_path / "allowed-signers"
        anchor.write_text(trust + "\n", encoding="ascii")
        current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        current_asset = signed_asset(
            executable.parent,
            tmp_path / "current-assets",
            version=current_version,
            upstream_url="http://127.0.0.1:1",
            key=key,
            trust=trust,
        )

        def interrupt_installed_lifecycle() -> None:
            with ExitStack() as cleanups:
                cleanups.callback(cleanup_runtime, ctx)
                installed = run_command(
                    executable,
                    environment,
                    "install",
                    "--asset",
                    str(current_asset),
                    "--trust-anchor",
                    str(anchor),
                    "--port",
                    str(port),
                    "--json",
                )
                assert installed["state"] == "installed"
                raise interruption("simulated test control-flow failure")

        with pytest.raises(interruption):
            interrupt_installed_lifecycle()

        assert native_service_projection(ctx) == isolated_before
        assert native_service_projection(canonical_ctx) == canonical_before


def test_native_cleanup_separates_registration_and_process_ownership(
    tmp_path: Path, *, mocker
) -> None:
    """Teardown removes the stable service after its generation leaves the selector."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    first = generation.context(ctx, "1" * 32)
    second = generation.context(ctx, "2" * 32)
    for owned in (first, second):
        Path(owned.payload_dir).mkdir(parents=True)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.side_effect = [second.executable, None]
    mocker.patch.object(release_fixtures.native_service, "adapter", return_value=service)
    mocker.patch.object(release_fixtures.process, "pids_naming_executable", return_value=[])

    cleanup_runtime(ctx)

    service.uninstall.assert_called_once_with(ctx)


def test_native_cleanup_continues_independent_processes_after_unproved_exit(
    tmp_path: Path, *, mocker
) -> None:
    """Retain the safe failure while still stopping the second owned generation."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.return_value = None
    mocker.patch.object(release_fixtures.native_service, "adapter", return_value=service)
    mocker.patch.object(release_fixtures, "_process_contexts", return_value=(ctx,))
    mocker.patch.object(release_fixtures.process, "pids_naming_executable", return_value=[41, 73])
    failure = errors.InstallError(
        "native process termination wait: access denied; exit is unproved"
    )
    terminate = mocker.patch.object(
        release_fixtures.process, "terminate_executable", side_effect=[failure, True]
    )

    with pytest.raises(errors.InstallError) as raised:
        cleanup_runtime(ctx)

    assert raised.value is failure
    assert [call.args[0] for call in terminate.call_args_list] == [41, 73]
