"""Native Windows locking and platform service-manager boundaries."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from threading import Thread
from typing import cast

import pytest

from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release import fixtures as release_fixtures
from tests.release.fixtures import ROOT
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import signing_inputs
from tests.serving.handoff.fixtures import free_port

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestSignedNativeLifecycle:
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
        key, trust, anchor = signing_inputs(tmp_path)
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
        assert not records.journal_path(ctx).exists()
        assert not records.transaction_root(ctx).exists()
        assert not any(path.is_file() or path.is_symlink() for path in home.rglob("*"))
