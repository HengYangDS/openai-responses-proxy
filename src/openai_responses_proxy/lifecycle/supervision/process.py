"""Discover and terminate only process identities owned by this installation."""

from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import dataclass

import psutil

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.context import RuntimeContext
from openai_responses_proxy.serving import roles as serving_roles


@dataclass(frozen=True)
class OwnedProcess:
    """A PID bound to the exact process entry path proven to own it."""

    pid: int
    executable: str
    created_at: float


def capture_generation(pid: int, expected_path: str) -> OwnedProcess | None:
    """Capture an already-proven PID as one exact process generation."""
    try:
        created_at = _created_at(psutil.Process(pid))
    except (OSError, TypeError, psutil.Error):
        return None
    return OwnedProcess(
        pid,
        os.path.normcase(os.path.realpath(os.path.abspath(expected_path))),
        created_at,
    )


def capture_executable(
    pid: int,
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None = None,
) -> OwnedProcess | None:
    """Capture one native process identity before later argv access can disappear."""
    try:
        candidate = psutil.Process(pid)
        argv = _argv(candidate)
        if not _argv_or_kernel_names_executable(
            candidate,
            argv,
            expected_path,
            roles=roles,
        ):
            return None
        return OwnedProcess(
            pid,
            os.path.normcase(os.path.realpath(os.path.abspath(expected_path))),
            _created_at(candidate),
        )
    except (OSError, TypeError, psutil.Error):
        return None


def wait_for_executable(
    pid: int,
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None = None,
    timeout_seconds: float = 5.0,
) -> OwnedProcess | None:
    """Boundedly capture one exact process after native service activation."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if owned := capture_executable(pid, expected_path, roles=roles):
            return owned
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))


def owned_process_alive(owned: OwnedProcess) -> bool:
    """Return whether the same captured PID generation is still alive."""
    try:
        candidate = psutil.Process(owned.pid)
        return (
            _created_at(candidate) == owned.created_at
            and _status(candidate) != psutil.STATUS_ZOMBIE
            and _is_running(candidate)
        )
    except (OSError, TypeError, psutil.Error):
        return False


def wait_for_exit(owned: OwnedProcess, *, timeout_seconds: float = 5.0) -> bool:
    """Boundedly prove that one captured process generation has exited."""
    deadline = time.monotonic() + timeout_seconds
    while owned_process_alive(owned):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def terminate_owned_process(owned: OwnedProcess, *, timeout_seconds: float = 5.0) -> bool:
    """Terminate a previously captured process without requiring later argv access."""
    candidate: psutil.Process | None = None
    phase = "capture"
    try:
        candidate = psutil.Process(owned.pid)
        phase = "generation"
        if _created_at(candidate) != owned.created_at:
            raise errors.InstallError(
                "native process termination generation: captured identity changed; exit is unproved"
            )
        phase = "status"
        if _status(candidate) == psutil.STATUS_ZOMBIE:
            return True
        phase = "signal"
        candidate.terminate()
        phase = "wait"
        candidate.wait(timeout=timeout_seconds)
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired as error:
        if candidate is not None and _timed_out_process_is_gone(candidate, owned):
            return True
        raise _termination_error(phase, error) from None
    except (OSError, TypeError, psutil.Error) as error:
        raise _termination_error(phase, error) from None
    return True


def listener_pids(port: int) -> list[int]:
    """Return PIDs listening on ``port``; identity verification is separate."""
    listeners: set[int] = set()
    try:
        processes = psutil.process_iter(["pid", "net_connections"])
        for candidate in processes:
            try:
                connections = candidate.info["net_connections"]
            except (KeyError, OSError, psutil.Error):
                continue
            if any(
                connection.status == psutil.CONN_LISTEN
                and getattr(connection.laddr, "port", None) == port
                for connection in connections or ()
            ):
                listeners.add(candidate.pid)
    except (OSError, psutil.Error):
        pass
    return sorted(listeners)


def process_command(pid: int) -> str:
    """Return a process command line for identity proof, or an empty string."""
    return shlex.join(process_argv(pid))


def process_argv(pid: int) -> list[str]:
    """Return the native argv for one process without guessing shell quoting."""
    try:
        return _argv(psutil.Process(pid))
    except (OSError, TypeError, psutil.Error):
        return []


def _process_inventory() -> list[tuple[int, list[str]]]:
    """Return one fail-closed host process inventory as PID and native argv."""
    inventory: list[tuple[int, list[str]]] = []
    try:
        processes = psutil.process_iter(["pid"])
        for candidate in processes:
            try:
                argv = _argv(candidate)
            except (OSError, TypeError, psutil.Error):
                continue
            if argv:
                inventory.append((candidate.pid, argv))
    except (OSError, psutil.Error):
        return []
    return inventory


def argv_names_executable(
    argv: list[str],
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None = None,
) -> bool:
    """Bind a native process to one exact executable and optional private role."""
    if not argv:
        return False
    expected = os.path.normcase(os.path.realpath(os.path.abspath(expected_path)))
    actual = os.path.normcase(os.path.realpath(os.path.abspath(argv[0])))
    if actual != expected:
        return False
    return roles is None or bool(roles.intersection(argv[1:]))


def pid_names_path(pid: int, expected_path: str) -> bool:
    """Re-read ``pid`` and prove that one argument is the exact expected path."""
    argv = process_argv(pid)
    executable = os.path.basename(argv[0]).lower() if argv else ""
    executable = executable.removesuffix(".exe")
    if len(argv) < 2 or re.fullmatch(r"python(?:w|3(?:\.\d+)?)?", executable) is None:
        return False
    expected = os.path.normcase(os.path.realpath(os.path.abspath(expected_path)))
    argument = os.path.normcase(os.path.realpath(os.path.abspath(argv[1])))
    return argument == expected


def pids_naming_path(expected_path: str) -> list[int]:
    """Return process IDs whose argv exactly names ``expected_path``."""
    return [pid for pid, _argv in _process_inventory() if pid_names_path(pid, expected_path)]


def pid_names_executable(
    pid: int,
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None = None,
) -> bool:
    """Re-read one PID and prove its exact native executable and private role."""
    argv = process_argv(pid)
    if argv_names_executable(argv, expected_path, roles=roles):
        return True
    if not argv or (roles is not None and not roles.intersection(argv[1:])):
        return False
    try:
        candidate = psutil.Process(pid)
        return _kernel_executable_matches(candidate, expected_path)
    except (OSError, psutil.Error):
        return False


def _argv_or_kernel_names_executable(
    candidate: psutil.Process,
    argv: list[str],
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None,
) -> bool:
    """Bind argv or the kernel executable to one native product identity."""
    if argv_names_executable(argv, expected_path, roles=roles):
        return True
    if not argv or (roles is not None and not roles.intersection(argv[1:])):
        return False
    return _kernel_executable_matches(candidate, expected_path)


def _kernel_executable_matches(candidate: psutil.Process, expected_path: str) -> bool:
    """Compare the kernel-reported executable after the role is proven by argv."""
    try:
        actual_path = _executable(candidate)
    except (OSError, TypeError, psutil.Error):
        return False
    expected = os.path.normcase(os.path.realpath(os.path.abspath(expected_path)))
    actual = os.path.normcase(os.path.realpath(os.path.abspath(actual_path)))
    return actual == expected


def _created_at(candidate: psutil.Process) -> float:
    """Narrow psutil's process-generation value at its boundary."""
    value: object = candidate.create_time()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("psutil process creation time must be numeric")
    return float(value)


def _status(candidate: psutil.Process) -> str:
    """Narrow psutil's process-status value at its boundary."""
    value: object = candidate.status()
    if not isinstance(value, str):
        raise TypeError("psutil process status must be text")
    return value


def _is_running(candidate: psutil.Process) -> bool:
    """Narrow psutil's process-liveness value at its boundary."""
    value: object = candidate.is_running()
    if not isinstance(value, bool):
        raise TypeError("psutil process liveness must be boolean")
    return value


def _argv(candidate: psutil.Process) -> list[str]:
    """Narrow psutil's process-argument value at its boundary."""
    try:
        value: object = candidate.cmdline()
    except SystemError as error:
        # psutil 7.2.2 on macOS raises its errno-0 argv denial as SystemError.
        # Remove after the lock includes https://github.com/giampaolo/psutil/pull/2854.
        if isinstance(error.__cause__, PermissionError):
            raise psutil.AccessDenied(candidate.pid) from error
        raise
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("psutil process argv must be a string list")
    return [item for item in value if isinstance(item, str)]


def _executable(candidate: psutil.Process) -> str:
    """Narrow psutil's executable value at its boundary."""
    value: object = candidate.exe()
    if not isinstance(value, str):
        raise TypeError("psutil process executable must be text")
    return value


def _timed_out_process_is_gone(candidate: psutil.Process, owned: OwnedProcess) -> bool:
    """Resolve a timed-out termination from the same process generation."""
    try:
        return (
            _created_at(candidate) == owned.created_at
            and _status(candidate) == psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return True
    except (OSError, TypeError, psutil.Error) as error:
        raise _termination_error("timeout observation", error) from None


def _termination_error(phase: str, error: BaseException) -> errors.InstallError:
    """Retain a closed failure vocabulary without exposing native exception text."""
    if isinstance(error, psutil.AccessDenied):
        reason = "access denied"
    elif isinstance(error, psutil.TimeoutExpired):
        reason = "deadline expired"
    elif isinstance(error, TypeError):
        reason = "invalid native observation"
    else:
        reason = errors.failure_summary(error)
    return errors.InstallError(f"native process termination {phase}: {reason}; exit is unproved")


def pids_naming_executable(
    expected_path: str, *, roles: set[str] | frozenset[str] | None = None
) -> list[int]:
    """Return PIDs bound to one native executable and optional private role."""
    return [
        pid
        for pid, _argv in _process_inventory()
        if pid_names_executable(pid, expected_path, roles=roles)
    ]


def verified_listener_pids(port: int, expected_path: str) -> list[int]:
    """Return listeners whose argv exactly names ``expected_path``."""
    return [pid for pid in listener_pids(port) if pid_names_path(pid, expected_path)]


def verified_proxy_listener_pids(context: RuntimeContext) -> list[int]:
    """Return listeners owned by the installed native product executable."""
    return [
        pid
        for pid in listener_pids(context.port)
        if pid_names_executable(
            pid,
            context.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
    ]


def terminate_executable(
    pid: int,
    expected_path: str,
    *,
    roles: set[str] | frozenset[str] | None = None,
    timeout_seconds: float = 5.0,
) -> bool:
    """Signal and prove exit of one exact native executable identity."""
    if not pid_names_executable(pid, expected_path, roles=roles):
        return False
    phase = "capture"
    try:
        candidate = psutil.Process(pid)
        if not pid_names_executable(pid, expected_path, roles=roles):
            return False
        phase = "signal"
        candidate.terminate()
        phase = "wait"
        candidate.wait(timeout=timeout_seconds)
    except psutil.NoSuchProcess:
        return True
    except (OSError, TypeError, psutil.Error) as error:
        raise _termination_error(phase, error) from None
    return True


def terminate_pid(pid: int, *, expected_path: str, timeout_seconds: float = 5.0) -> bool:
    """Signal an exact process identity and prove that identity is gone.

    The command line is re-read immediately before the signal. Afterwards, PID
    disappearance or a different occupant proves that the original process
    exited; the new occupant is never signalled.
    """
    if not pid_names_path(pid, expected_path):
        return False
    phase = "capture"
    try:
        candidate = psutil.Process(pid)
        if not pid_names_path(pid, expected_path):
            return False
        phase = "signal"
        candidate.terminate()
        phase = "wait"
        candidate.wait(timeout=timeout_seconds)
    except psutil.NoSuchProcess:
        return True
    except (OSError, TypeError, psutil.Error) as error:
        raise _termination_error(phase, error) from None
    return True
