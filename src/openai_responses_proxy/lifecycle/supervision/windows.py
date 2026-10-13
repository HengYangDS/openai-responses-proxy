"""Persist the watchdog as one current-user scheduled task."""

from __future__ import annotations

import getpass
import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import roles as serving_roles

# A fixed past boundary so the repeating time trigger is always active; the
# repetition, not this date, drives every self-heal relaunch.
_SELF_HEAL_START_BOUNDARY = "2020-01-01T00:00:00"
_TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
# Only an exact local schtasks query may interpret these task/path absence codes.
_TASK_NOT_FOUND = frozenset((0x80070002, 0x80070003))
ET.register_namespace("", _TASK_NAMESPACE)


def _element(
    parent: ET.Element, name: str, text: str | None = None, **attributes: str
) -> ET.Element:
    child = ET.SubElement(parent, f"{{{_TASK_NAMESPACE}}}{name}", attributes)
    child.text = text
    return child


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def render_task_xml(ctx: runtime_spec.NativeServiceContext) -> bytes:
    """Serialize one Task Scheduler projection as self-declared UTF-16 bytes."""
    root = ET.Element(f"{{{_TASK_NAMESPACE}}}Task", {"version": "1.2"})
    registration = _element(root, "RegistrationInfo")
    _element(registration, "Description", f"{product_identity.DISPLAY_NAME} watchdog")
    triggers = _element(root, "Triggers")
    logon = _element(triggers, "LogonTrigger")
    _element(logon, "Enabled", "true")
    _element(logon, "UserId", _current_user())
    timed = _element(triggers, "TimeTrigger")
    _element(timed, "Enabled", "true")
    _element(timed, "StartBoundary", _SELF_HEAL_START_BOUNDARY)
    repetition = _element(timed, "Repetition")
    _element(repetition, "Interval", "PT1M")
    _element(repetition, "StopAtDurationEnd", "false")
    principals = _element(root, "Principals")
    principal = _element(principals, "Principal", id="Author")
    _element(principal, "LogonType", "InteractiveToken")
    _element(principal, "RunLevel", "LeastPrivilege")
    settings = _element(root, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("ExecutionTimeLimit", "PT0S"),
        ("Enabled", "true"),
    ):
        _element(settings, name, value)
    restart = _element(settings, "RestartOnFailure")
    _element(restart, "Interval", "PT1M")
    _element(restart, "Count", "999")
    actions = _element(root, "Actions", Context="Author")
    execute = _element(actions, "Exec")
    _element(execute, "Command", ctx.executable)
    _element(execute, "Arguments", serving_roles.WATCHDOG_MODE)
    _element(execute, "WorkingDirectory", ctx.payload_dir)
    ET.indent(root, space="  ")
    rendered = ET.tostring(root, encoding="utf-16", xml_declaration=True)
    assert isinstance(rendered, bytes)
    return rendered


def _task_xml(ctx: runtime_spec.NativeServiceContext) -> str | None:
    """Read one exact task; only its native not-found code proves absence."""
    try:
        completed = subprocess.run(
            ["schtasks", "/query", "/tn", ctx.service_id, "/xml", "/hresult"],
            capture_output=True,
            check=False,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5.0,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise errors.InstallError("scheduled task state is unproved") from None
    result = completed.returncode & 0xFFFFFFFF
    if result in _TASK_NOT_FOUND:
        return None
    if result != 0:
        raise errors.InstallError(f"scheduled task state is unproved (HRESULT {result:#010x})")
    return completed.stdout


def configured_executable(ctx: runtime_spec.NativeServiceContext) -> str | None:
    """Return one proved watchdog executable, or proved task absence."""
    xml = _task_xml(ctx)
    if xml is None:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise errors.InstallError("scheduled task XML is unproved") from None
    namespace = {"task": _TASK_NAMESPACE}
    commands = root.findall(".//task:Actions/task:Exec/task:Command", namespace)
    arguments = root.findall(".//task:Actions/task:Exec/task:Arguments", namespace)
    if (
        len(commands) != 1
        or len(arguments) != 1
        or not commands[0].text
        or arguments[0].text != serving_roles.WATCHDOG_MODE
    ):
        raise errors.InstallError("scheduled task watchdog identity is unproved")
    return commands[0].text


def projection_is_current(ctx: runtime_spec.NativeServiceContext) -> bool:
    """Return whether the registered task action runs this release's watchdog.

    Task Scheduler normalizes the rest of an exported definition, so only the
    action is comparable with this release's rendering.
    """
    return configured_executable(ctx) == ctx.executable


def install(ctx: runtime_spec.NativeServiceContext) -> None:
    """Replace and prove the Windows scheduled watchdog task."""
    previous_executable = configured_executable(ctx)
    predecessors = []
    if previous_executable is not None:
        for pid in process.pids_naming_executable(
            previous_executable, roles={serving_roles.WATCHDOG_MODE}
        ):
            predecessor = process.capture_executable(
                pid,
                previous_executable,
                roles={serving_roles.WATCHDOG_MODE},
            )
            if predecessor is None:
                raise errors.InstallError("scheduled watchdog process identity is unproved")
            predecessors.append(predecessor)
    for predecessor in predecessors:
        if not process.terminate_owned_process(predecessor):
            raise errors.InstallError(
                f"scheduled predecessor watchdog {predecessor.pid} exit is unproved"
            )
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as stream:
        xml_path = stream.name
        stream.write(render_task_xml(ctx))
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", ctx.service_id, "/f"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        created = subprocess.run(
            ["schtasks", "/create", "/tn", ctx.service_id, "/xml", xml_path],
            capture_output=True,
            check=False,
            text=True,
        )
        if created.returncode:
            raise errors.InstallError(f"schtasks create failed (exit {created.returncode})")
    finally:
        os.unlink(xml_path)
    started = subprocess.run(
        ["schtasks", "/run", "/tn", ctx.service_id],
        capture_output=True,
        check=False,
        text=True,
    )
    if started.returncode:
        raise errors.InstallError(f"schtasks run failed (exit {started.returncode})")
    if configured_executable(ctx) != ctx.executable:
        raise errors.InstallError("scheduled watchdog task executable is unproved")
    if _wait_for_watchdog(ctx) is None:
        raise errors.InstallError("scheduled successor watchdog process identity is unproved")


def _running_watchdog_pids(ctx: runtime_spec.NativeServiceContext) -> list[int]:
    """Return PIDs exactly naming this installation's native watchdog role."""
    return process.pids_naming_executable(ctx.executable, roles={serving_roles.WATCHDOG_MODE})


def _wait_for_watchdog(
    ctx: runtime_spec.NativeServiceContext, timeout_seconds: float = 5.0
) -> process.OwnedProcess | None:
    """Boundedly prove the one watchdog started by the registered task."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        watchdogs = _running_watchdog_pids(ctx)
        if len(watchdogs) == 1:
            watchdog = process.capture_executable(
                watchdogs[0],
                ctx.executable,
                roles={serving_roles.WATCHDOG_MODE},
            )
            if watchdog is not None and process.owned_process_alive(watchdog):
                return watchdog
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))


def uninstall(ctx: runtime_spec.NativeServiceContext) -> None:
    """Stop and remove only this installation's scheduled watchdog task."""
    deleted = subprocess.run(
        ["schtasks", "/delete", "/tn", ctx.service_id, "/f"],
        capture_output=True,
        check=False,
        text=True,
    )
    if deleted.returncode:
        if status(ctx) != "absent":
            raise errors.InstallError(f"schtasks delete failed (exit {deleted.returncode})")
    elif status(ctx) != "absent":
        raise errors.InstallError("scheduled watchdog task remains registered after deletion")
    # PID discovery and termination are separate observations. Re-read every
    # candidate immediately before signalling so PID reuse cannot target a new
    # process that appeared between them.
    for pid in _running_watchdog_pids(ctx):
        if not process.terminate_executable(
            pid, ctx.executable, roles={serving_roles.WATCHDOG_MODE}
        ):
            raise errors.InstallError(f"verified watchdog {pid} exit is unproved")
    if remaining := _running_watchdog_pids(ctx):
        raise errors.InstallError(f"verified watchdogs remain: {remaining}")


def status(ctx: runtime_spec.NativeServiceContext) -> str:
    """Return the Windows scheduled task's read-only status classification."""
    configured = configured_executable(ctx)
    if configured is None:
        return "absent"
    if process.pids_naming_executable(configured, roles={serving_roles.WATCHDOG_MODE}):
        return "running"
    return "installed"
