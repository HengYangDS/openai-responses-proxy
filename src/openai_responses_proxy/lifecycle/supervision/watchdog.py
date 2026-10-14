"""Keep one installed proxy listener available without crossing transactions.

The native user service owns watchdog persistence. This process starts the
absolute installed entrypoint only while no payload transaction owns the
runtime. Logs are bounded and secret-safe; request payloads never cross this
boundary.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.runtime import bounded_log
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from openai_responses_proxy.serving import roles as serving_roles

SETTINGS = runtime_config.load()
HOST, PORT = SETTINGS.listener
EXECUTABLE = str(Path(sys.executable).resolve())
LISTENER_MODE = serving_roles.LISTENER_MODE
CHECK_INTERVAL = SETTINGS.watchdog_interval
MAX_BACKOFF = SETTINGS.watchdog_max_backoff
LOG_PATH = SETTINGS.watchdog_log.path
_WINDOWS_DETACH_FLAGS = 0x00000008 | 0x00000200


LOG_MAX_BYTES = SETTINGS.watchdog_log.max_bytes
LOG_BACKUP_COUNT = SETTINGS.watchdog_log.backup_count


def _reap_children(children: list[subprocess.Popen[bytes]]) -> None:
    """Reap exited listener children so the watchdog never leaves zombies."""
    children[:] = [child for child in children if child.poll() is None]


def _log(msg: str) -> None:
    bounded_log.append(Path(LOG_PATH), msg, max_bytes=LOG_MAX_BYTES, backup_count=LOG_BACKUP_COUNT)


def is_proxy_up(host: str = HOST, port: int = PORT, timeout: float = 2.0) -> bool:
    """Return whether a TCP listener accepts a connection at ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def spawn_proxy(executable: str) -> subprocess.Popen[bytes] | None:
    """Start the proxy detached from this watchdog so it outlives us.

    Uses the exact installed executable path. Detaches via ``start_new_session``
    on POSIX and ``DETACHED_PROCESS`` on Windows so a watchdog restart never
    signals the proxy.
    """
    if not os.path.exists(executable):
        _log("ERROR installed proxy executable is unavailable")
        return None
    try:
        command = [executable, LISTENER_MODE]
        environment = native_process_environment(
            install_root=os.path.dirname(os.path.dirname(executable)),
            state_root=runtime_config.state_dir(),
            restart_frozen_runtime=True,
        )
        if os.name == "nt":
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=_WINDOWS_DETACH_FLAGS,
                env=environment,
            )
        else:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                env=environment,
                start_new_session=True,
            )
        _log(f"spawned proxy pid={proc.pid}")
        return proc
    except OSError as exc:
        _log(f"ERROR failed to spawn proxy: {exc.__class__.__name__}")
        return None


def run(max_iterations: int | None = None) -> None:
    """Resident supervise loop. ``max_iterations`` bounds the loop for testing."""
    _log(
        f"watchdog starting: guard {HOST}:{PORT} every {CHECK_INTERVAL}s "
        f"executable={Path(EXECUTABLE).name}"
    )
    consecutive_failures = 0
    iterations = 0
    children: list[subprocess.Popen[bytes]] = []
    while True:
        executable = EXECUTABLE
        _reap_children(children)
        sleep_for = CHECK_INTERVAL
        available = is_proxy_up()
        if not available:
            try:
                ctx = runtime_context.create(executable=EXECUTABLE, port=PORT)
                executable = ctx.executable
                transaction = records.status(ctx)
            except errors.InstallError:
                transaction = {"state": "invalid"}
                _log("ERROR installed runtime ownership is invalid; refusing to start")
            if transaction is not None and transaction.get("state") != "activated":
                available = True
                _log("payload transaction owns runtime startup; watchdog is waiting")
        if not available:
            _log(f"proxy unavailable on {HOST}:{PORT} — starting it")
            if child := spawn_proxy(executable):
                children.append(child)
            consecutive_failures += 1
            settle = min(MAX_BACKOFF, CHECK_INTERVAL * consecutive_failures)
            sleep_for = settle
        else:
            consecutive_failures = 0

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        time.sleep(sleep_for)
