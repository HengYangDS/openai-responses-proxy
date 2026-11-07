"""Contracts for deterministic native-product process environments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import product_identity
from openai_responses_proxy.runtime.process_environment import native_process_environment

if TYPE_CHECKING:
    import pytest


def test_isolation_preserves_the_host_execution_substrate(tmp_path: Path) -> None:
    """Keep platform services and unrelated host state available."""
    host_path = os.pathsep.join(("host-tools", "system-tools"))
    environment = native_process_environment(
        user_home=tmp_path / "home",
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        inherited={
            "PATH": host_path,
            "SystemRoot": r"C:\Windows",
            "HOST_EXECUTION_CONTEXT": "preserved",
            product_identity.environment_name("STALE"): "retired",
        },
    )

    assert environment["PATH"] == host_path
    assert environment["SystemRoot"] == r"C:\Windows"
    assert environment["HOST_EXECUTION_CONTEXT"] == "preserved"
    assert product_identity.environment_name("STALE") not in environment


def test_isolation_removes_ambient_python_runtime_state(tmp_path: Path) -> None:
    """Keep a native artifact independent from an ambient Python runtime."""
    environment = native_process_environment(
        user_home=tmp_path / "home",
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        inherited={
            "PYTHONHOME": str(tmp_path / "foreign-python"),
            "PythonPath": str(tmp_path / "foreign-packages"),
            "PYTHONSTARTUP": str(tmp_path / "startup.py"),
            "PYINSTALLER_RESET_ENVIRONMENT": "0",
            "_PYI_ARCHIVE_FILE": str(tmp_path / "foreign.pkg"),
        },
    )

    assert not any(
        name.upper().startswith(("PYTHON", "PYINSTALLER_", "_PYI_")) for name in environment
    )


def test_isolation_accepts_an_explicit_empty_command_search_path(tmp_path: Path) -> None:
    """Prove a native artifact need not discover Python through ``PATH``."""
    empty_path = tmp_path / "empty-path"
    environment = native_process_environment(
        user_home=tmp_path / "home",
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        command_search_path=empty_path,
        inherited={"PATH": "host-tools"},
    )

    assert environment["PATH"] == str(empty_path)


def test_isolation_redirects_every_user_and_product_root(tmp_path: Path) -> None:
    """Keep native lifecycle effects inside the declared isolated roots."""
    home = tmp_path / "home"
    install = tmp_path / "payload"
    state = tmp_path / "state"
    environment = native_process_environment(
        user_home=home,
        install_root=install,
        state_root=state,
        inherited={
            "APPDATA": "foreign-app-data",
            "LOCALAPPDATA": "foreign-local-app-data",
            "XDG_BIN_HOME": "foreign-bin",
            "XDG_CACHE_HOME": "foreign-cache",
            "XDG_CONFIG_HOME": "foreign-config",
            "XDG_DATA_HOME": "foreign-data",
            "XDG_STATE_HOME": "foreign-state",
        },
    )

    assert environment[product_identity.environment_name("HOME")] == str(install)
    assert environment[product_identity.environment_name("STATE_HOME")] == str(state)
    assert environment["APPDATA"] == str(home / "AppData" / "Roaming")
    assert environment["HOME"] == str(home)
    assert environment["LOCALAPPDATA"] == str(home / "AppData" / "Local")
    assert environment["USERPROFILE"] == str(home)
    assert environment["XDG_BIN_HOME"] == str(home / ".local" / "bin")
    assert environment["XDG_CACHE_HOME"] == str(home / ".cache")
    assert environment["XDG_CONFIG_HOME"] == str(home / ".config")
    assert environment["XDG_DATA_HOME"] == str(home / ".local" / "share")
    assert environment["XDG_STATE_HOME"] == str(home / ".local" / "state")


def test_product_process_keeps_the_user_root_and_projects_runtime_roots(
    tmp_path: Path,
) -> None:
    """Run installed product roles in the real user domain with owned state."""
    environment = native_process_environment(
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        inherited={"HOME": "/Users/operator", "USERPROFILE": r"C:\Users\operator"},
    )

    assert environment["HOME"] == "/Users/operator"
    assert environment["USERPROFILE"] == r"C:\Users\operator"
    assert environment[product_identity.environment_name("HOME")] == str(tmp_path / "payload")
    assert environment[product_identity.environment_name("STATE_HOME")] == str(tmp_path / "state")


def test_relative_roots_are_bound_before_the_child_changes_directory(
    tmp_path: Path, *, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep all owned roots stable across a subprocess working-directory change."""
    monkeypatch.chdir(tmp_path)
    environment = native_process_environment(
        user_home=Path("sandbox/home"),
        install_root=Path("sandbox/payload"),
        state_root=Path("sandbox/state"),
        inherited={},
    )

    assert environment[product_identity.environment_name("HOME")] == str(
        tmp_path / "sandbox/payload"
    )
    assert environment[product_identity.environment_name("STATE_HOME")] == str(
        tmp_path / "sandbox/state"
    )
    assert environment["HOME"] == str(tmp_path / "sandbox/home")
    assert environment["XDG_BIN_HOME"] == str(tmp_path / "sandbox/home/.local/bin")


def test_product_process_restarts_the_pyinstaller_environment(tmp_path: Path) -> None:
    """Request a fresh frozen runtime after inherited bootloader state is removed."""
    environment = native_process_environment(
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        restart_frozen_runtime=True,
        inherited={"PYINSTALLER_RESET_ENVIRONMENT": "0", "_PYI_ARCHIVE_FILE": "foreign.pkg"},
    )

    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "_PYI_ARCHIVE_FILE" not in environment


def test_isolation_preserves_the_linux_user_bus(tmp_path: Path) -> None:
    """Let isolated native commands reach the current user's systemd bus."""
    environment = native_process_environment(
        user_home=tmp_path / "home",
        install_root=tmp_path / "payload",
        state_root=tmp_path / "state",
        inherited={
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        },
    )

    assert environment["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert environment["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
