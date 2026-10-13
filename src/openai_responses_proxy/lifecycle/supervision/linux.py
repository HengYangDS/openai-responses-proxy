"""Persist the watchdog through the systemd user service manager."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles

UNIT_TEMPLATE = f"""[Unit]
Description={product_identity.DISPLAY_NAME} watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={{executable}} {{watchdog_mode}}
KillMode=process
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def _has_user_systemd() -> bool:
    if not shutil.which("systemctl"):
        return False
    result = subprocess.run(
        ["systemctl", "--user", "show", "--property=SystemState", "--value"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _unit_path(ctx: runtime_spec.NativeServiceContext) -> str:
    """Return the systemd user-unit carrier owned by this service identity."""
    return str(Path(ctx.user_home, ".config", "systemd", "user", f"{ctx.service_id}.service"))


def _unit_value(value: str) -> str:
    """Quote one literal systemd unit value without enabling specifier expansion."""
    if any(character in value for character in ("\0", "\n", "\r")):
        raise errors.InstallError("systemd service value contains a control character")
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def render_unit(ctx: runtime_spec.NativeServiceContext) -> str:
    """Render the user-level systemd watchdog unit for this installation."""
    return UNIT_TEMPLATE.format(
        executable=f'"{_unit_value(ctx.executable)}"',
        watchdog_mode=serving_roles.WATCHDOG_MODE,
    )


def configured_executable(ctx: runtime_spec.NativeServiceContext) -> str | None:
    """Return the executable from one unambiguous product systemd unit."""
    try:
        lines = Path(_unit_path(ctx)).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    commands = [line.removeprefix("ExecStart=") for line in lines if line.startswith("ExecStart=")]
    if len(commands) != 1:
        return None
    try:
        arguments = shlex.split(commands[0], posix=True)
    except ValueError:
        return None
    if len(arguments) != 2 or arguments[1] != serving_roles.WATCHDOG_MODE:
        return None
    return arguments[0].replace("%%", "%")


def projection_is_current(ctx: runtime_spec.NativeServiceContext) -> bool:
    """Return whether the product systemd unit is this release's rendering."""
    try:
        content = Path(_unit_path(ctx)).read_bytes()
    except OSError:
        return False
    return content == render_unit(ctx).encode("utf-8")


def _install_systemd(ctx: runtime_spec.NativeServiceContext) -> None:
    unit = Path(_unit_path(ctx))
    service = f"{ctx.service_id}.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_unit(ctx), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    enabled = subprocess.run(
        ["systemctl", "--user", "enable", str(unit)],
        capture_output=True,
        check=False,
        text=True,
    )
    if enabled.returncode != 0:
        raise errors.InstallError(f"systemctl enable failed (exit {enabled.returncode})")
    restarted = subprocess.run(
        ["systemctl", "--user", "restart", service],
        capture_output=True,
        check=False,
        text=True,
    )
    if restarted.returncode != 0:
        raise errors.InstallError(f"systemctl restart failed (exit {restarted.returncode})")
    observed = subprocess.run(
        ["systemctl", "--user", "show", service, "--property=MainPID", "--value"],
        capture_output=True,
        check=False,
        text=True,
    )
    try:
        watchdog_pid = int(observed.stdout.strip()) if observed.returncode == 0 else 0
    except ValueError:
        watchdog_pid = 0
    watchdog = (
        process.wait_for_executable(
            watchdog_pid,
            ctx.executable,
            roles={serving_roles.WATCHDOG_MODE},
        )
        if watchdog_pid > 0
        else None
    )
    if watchdog is None or not process.owned_process_alive(watchdog):
        raise errors.InstallError("systemd successor watchdog process identity is unproved")


def install(ctx: runtime_spec.NativeServiceContext) -> None:
    """Install and start the Linux user-level watchdog service."""
    if _has_user_systemd():
        _install_systemd(ctx)
    else:
        raise errors.NativeServiceUnavailableError(
            "a reachable systemd user manager is required for Linux installation; "
            "enable a systemd user session and retry installation"
        )


def uninstall(ctx: runtime_spec.NativeServiceContext) -> None:
    """Stop and remove only this installation's Linux watchdog service."""
    unit = Path(_unit_path(ctx))
    service = f"{ctx.service_id}.service"
    registered = status(ctx) != "absent"
    if registered or unit.exists():
        if not shutil.which("systemctl"):
            raise errors.InstallError("systemctl is unavailable; service removal is unproven")
        disabled = subprocess.run(
            ["systemctl", "--user", "disable", "--now", service],
            capture_output=True,
            check=False,
            text=True,
        )
        if disabled.returncode and registered:
            raise errors.InstallError(f"systemctl disable failed (exit {disabled.returncode})")
        unit.unlink(missing_ok=True)
        reloaded = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False,
            text=True,
        )
        if reloaded.returncode:
            raise errors.InstallError("systemctl daemon-reload failed after unit removal")
        if status(ctx) != "absent":
            raise errors.InstallError("systemd watchdog remains registered after removal")
    watchdogs = process.pids_naming_executable(ctx.executable, roles={serving_roles.WATCHDOG_MODE})
    for pid in watchdogs:
        if not process.terminate_executable(
            pid, ctx.executable, roles={serving_roles.WATCHDOG_MODE}
        ):
            raise errors.InstallError(f"verified watchdog {pid} exit is unproved")
    if remaining := process.pids_naming_executable(
        ctx.executable, roles={serving_roles.WATCHDOG_MODE}
    ):
        raise errors.InstallError(f"verified watchdogs remain: {remaining}")


def status(ctx: runtime_spec.NativeServiceContext) -> str:
    """Return the Linux service manager's read-only status classification."""
    unit = Path(_unit_path(ctx))
    if shutil.which("systemctl"):
        observed = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                f"{ctx.service_id}.service",
                "--property=LoadState",
                "--property=ActiveState",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        properties = dict(
            line.split("=", 1) for line in observed.stdout.splitlines() if "=" in line
        )
        if observed.returncode or not all(
            properties.get(key) for key in ("LoadState", "ActiveState")
        ):
            raise errors.InstallError("systemd service state is unproven; query the user manager")
        if properties["LoadState"] != "not-found":
            return "running" if properties.get("ActiveState") == "active" else "installed"
    return "installed" if unit.exists() else "absent"
