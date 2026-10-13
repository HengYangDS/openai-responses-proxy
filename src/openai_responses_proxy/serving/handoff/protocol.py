"""Bounded wire and process capabilities for protocol-v2 listener handoff.

The handoff transaction owns lifecycle state in :mod:`handoff`; this module
owns only message framing, the parent/child control channel, inherited-listener
transfer, and the loopback health proof used at the commit barrier.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from typing import IO
from typing import Protocol
from typing import TypedDict
from typing import cast

from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime import loopback
from openai_responses_proxy.runtime.process_environment import native_process_environment

type JsonObject = dict[str, object]
type ReadOnlyJsonObject = Mapping[str, object]


class _ShareableSocket(Protocol):
    """Windows socket capability exposed only on supported interpreters."""

    def share(self, _process_id: int, /) -> bytes:
        """Serialize this socket for one Windows process."""


class _SocketShareModule(Protocol):
    """Windows socket reconstruction capability."""

    def fromshare(self, info: bytes) -> socket.socket:
        """Reconstruct one socket from serialized Windows state."""


class _PopenKwargs(TypedDict, total=False):
    """Keyword arguments for a binary handoff child process."""

    stdin: int
    stdout: int
    stderr: int
    close_fds: bool
    creationflags: int
    pass_fds: tuple[int, ...]
    start_new_session: bool
    env: dict[str, str]


class ChildProcessContext(Protocol):
    """Process capability required to launch one replacement listener."""

    @property
    def executable(self) -> Path:
        """Return the immutable replacement product executable."""

    @property
    def state_root(self) -> Path:
        """Return the product state root inherited by this process."""


HANDOFF_PROTOCOL_VERSION = 2
HANDOFF_CONTROL_MAX_BYTES = 32 * 1024
HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS = 5.0
HANDOFF_STARTUP_TIMEOUT_SECONDS = 30.0


class HandoffError(RuntimeError):
    """A bounded rolling-handoff operation could not be completed safely."""


def popen_kwargs(listener_fd: int | None, *, is_windows: bool) -> _PopenKwargs:
    """Return platform-specific, pipe-only child process settings."""
    kwargs: _PopenKwargs = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if is_windows:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    else:
        if listener_fd is None:
            raise HandoffError("POSIX handoff requires a listener fd")
        kwargs["pass_fds"] = (listener_fd,)
        kwargs["start_new_session"] = True
    return kwargs


def _encode_control_message(message: ReadOnlyJsonObject, error: str) -> bytes:
    encoded = (
        json.dumps(message, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "ascii"
        )
        + b"\n"
    )
    if len(encoded) > HANDOFF_CONTROL_MAX_BYTES:
        raise HandoffError(error)
    return encoded


def _decode_control_message(
    line: bytes,
    *,
    closed_error: Exception,
    limit_error: str,
    invalid_error: str,
    object_error: str,
) -> JsonObject:
    if not line:
        raise closed_error
    if len(line) > HANDOFF_CONTROL_MAX_BYTES or not line.endswith(b"\n"):
        raise HandoffError(limit_error)
    try:
        message: object = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError(invalid_error) from exc
    if not isinstance(message, dict) or not all(isinstance(key, str) for key in message):
        raise HandoffError(object_error)
    return {key: value for key, value in message.items() if isinstance(key, str)}


class HandoffChild:
    """One launcher-owned control channel to a replacement runtime."""

    def __init__(self, process: subprocess.Popen[bytes]):
        """Bind the launcher's exact child process and control pipes."""
        if process.stdin is None or process.stdout is None:
            raise HandoffError("handoff child pipes are unavailable")
        self.process = process
        self._input: IO[bytes] = process.stdin
        self._output: IO[bytes] = process.stdout
        self._send_lock = threading.Lock()
        self._events: queue.Queue[JsonObject | Exception] = queue.Queue()
        self._reader_started = False
        self._reader_lock = threading.Lock()
        self._runtime_pid: int | None = None

    @property
    def runtime_pid(self) -> int:
        """Return the runtime process that owns protocol and listener identity."""
        if self._runtime_pid is None:
            raise HandoffError("handoff child runtime has not started")
        return self._runtime_pid

    def send_message(self, message: JsonObject) -> None:
        """Send one bounded canonical control message to the replacement child."""
        encoded = _encode_control_message(message, "handoff message exceeds the control limit")
        with self._send_lock:
            try:
                self._input.write(encoded)
                self._input.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise HandoffError("handoff control pipe write failed") from exc

    def _start_reader(self) -> None:
        with self._reader_lock:
            if self._reader_started:
                return
            self._reader_started = True

            def read_events() -> None:
                try:
                    while True:
                        line = self._output.readline(HANDOFF_CONTROL_MAX_BYTES + 1)
                        try:
                            message = _decode_control_message(
                                line,
                                closed_error=HandoffError("handoff child control pipe closed"),
                                limit_error="handoff child message exceeds the control limit",
                                invalid_error="handoff child emitted invalid JSON",
                                object_error="handoff child message must be an object",
                            )
                        except HandoffError as exc:
                            self._events.put(exc)
                            return
                        self._events.put(message)
                except (OSError, ValueError):
                    self._events.put(HandoffError("handoff child control pipe read failed"))

            threading.Thread(
                target=read_events, daemon=True, name="responses-proxy-handoff-reader"
            ).start()

    def recv_message(self, timeout: float) -> JsonObject:
        """Receive one validated child message within ``timeout`` seconds."""
        self._start_reader()
        try:
            item = self._events.get(timeout=max(0.01, float(timeout)))
        except queue.Empty as exc:
            raise HandoffError("handoff child response timed out") from exc
        if isinstance(item, Exception):
            raise item
        return item

    def await_runtime(self, timeout: float) -> int:
        """Bind the control channel to the exact runtime PID announced by the child."""
        message = self.recv_message(timeout)
        pid = message.get("pid")
        if (
            set(message) != {"type", "pid"}
            or message.get("type") != "started"
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
        ):
            raise HandoffError("handoff child STARTED identity mismatch")
        self._runtime_pid = pid
        return pid

    def _stop_bounded(self, action: Callable[[], None], timeout: float) -> bool:
        if self.process.poll() is not None:
            return True
        try:
            action()
            self.process.wait(timeout=max(0.01, float(timeout)))
            return True
        except (OSError, subprocess.TimeoutExpired):
            return self.process.poll() is not None

    def terminate_bounded(self, timeout: float) -> bool:
        """Request graceful child termination and prove exit within the bound."""
        return self._stop_bounded(self.process.terminate, timeout)

    def kill_bounded(self, timeout: float) -> bool:
        """Force child termination and prove exit within the bound."""
        return self._stop_bounded(self.process.kill, timeout)


def spawn_child(
    listener: socket.socket,
    expected: ReadOnlyJsonObject,
    context: ChildProcessContext,
    *,
    is_windows: bool | None = None,
    startup_timeout_seconds: float = HANDOFF_STARTUP_TIMEOUT_SECONDS,
) -> HandoffChild:
    """Spawn a replacement, bind its runtime PID, and send PREPARE."""
    windows = os.name == "nt" if is_windows is None else bool(is_windows)
    listener_fd = None if windows else listener.fileno()
    kwargs = popen_kwargs(listener_fd, is_windows=windows)
    env = native_process_environment(
        install_root=context.executable.parent.parent,
        state_root=context.state_root,
        restart_frozen_runtime=True,
    )
    kwargs["env"] = env
    process: subprocess.Popen[bytes] = subprocess.Popen(
        [str(context.executable), "--internal-handoff-child"],
        **kwargs,
    )
    child = HandoffChild(process)
    message = {
        "type": "prepare",
        "protocol_version": HANDOFF_PROTOCOL_VERSION,
        **{
            key: expected[key]
            for key in (
                "transaction_id",
                "release",
                "serving_payload_sha256",
                "release_receipt_sha256",
                "manifest_sha256",
            )
        },
    }
    try:
        runtime_pid = child.await_runtime(startup_timeout_seconds)
        if windows:
            share: object = getattr(listener, "share", None)
            if not callable(share):
                raise HandoffError("Windows listener sharing is unavailable")
            try:
                shared = cast(Callable[[int], bytes], share)(runtime_pid)
            except Exception as exc:
                raise HandoffError("Windows listener sharing failed") from exc
            message["listener_share_b64"] = base64.b64encode(shared).decode("ascii")
        else:
            message["listener_fd"] = listener_fd
        child.send_message(message)
    except Exception:
        if not child.terminate_bounded(HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS):
            child.kill_bounded(HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS)
        raise
    return child


def listener_from_prepare(message: ReadOnlyJsonObject) -> socket.socket:
    """Reconstruct the already-listening socket from a validated PREPARE."""
    if "listener_share_b64" in message:
        encoded = message.get("listener_share_b64")
        if not isinstance(encoded, str) or len(encoded) > HANDOFF_CONTROL_MAX_BYTES:
            raise HandoffError("invalid Windows listener share")
        try:
            shared = base64.b64decode(encoded.encode("ascii"), validate=True)
            fromshare: object = getattr(socket, "fromshare", None)
            if not callable(fromshare):
                raise HandoffError("Windows listener reconstruction is unavailable")
            return cast(Callable[[bytes], socket.socket], fromshare)(shared)
        except Exception as exc:
            raise HandoffError("Windows listener reconstruction failed") from exc
    listener_fd = message.get("listener_fd")
    if not isinstance(listener_fd, int) or listener_fd < 0:
        raise HandoffError("invalid inherited listener fd")
    return socket.socket(fileno=listener_fd)


def _read_health(port: int, *, timeout_seconds: float) -> JsonObject:
    """Read one loopback-only health snapshot through the shared listener."""
    url = runtime_config.loopback_url(int(port), "/healthz")
    request = urllib.request.Request(url, method="GET")
    with loopback.open_request(
        request,
        timeout_seconds=max(0.1, float(timeout_seconds)),
    ) as response:
        payload = response.read(HANDOFF_CONTROL_MAX_BYTES + 1)
    if len(payload) > HANDOFF_CONTROL_MAX_BYTES:
        raise HandoffError("handoff health response exceeds the control limit")
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("handoff health response is invalid") from exc
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise HandoffError("handoff health response must be an object")
    return {key: value for key, value in decoded.items() if isinstance(key, str)}


def probe_health(
    port: int,
    *,
    timeout_seconds: float,
    expected: ReadOnlyJsonObject | None = None,
) -> JsonObject:
    """Wait until the inherited listener serves the expected child identity."""
    if expected is None:
        return _read_health(port, timeout_seconds=timeout_seconds)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HandoffError("handoff child health identity did not converge")
        try:
            health = _read_health(port, timeout_seconds=min(remaining, 1.0))
        except Exception:
            health = None
        if health is not None and all(
            health.get(field) == value for field, value in expected.items()
        ):
            return health
        time.sleep(min(0.05, remaining))


def read_control_message(stream: IO[bytes]) -> JsonObject:
    """Read and validate one bounded newline-delimited control message."""
    return _decode_control_message(
        stream.readline(HANDOFF_CONTROL_MAX_BYTES + 1),
        closed_error=EOFError("handoff control pipe closed"),
        limit_error="handoff control message exceeds the limit",
        invalid_error="handoff control message is invalid",
        object_error="handoff control message must be an object",
    )


def write_control_message(stream: IO[bytes], message: ReadOnlyJsonObject) -> None:
    """Write one bounded newline-delimited control message."""
    stream.write(_encode_control_message(message, "handoff control message exceeds the limit"))
    stream.flush()
