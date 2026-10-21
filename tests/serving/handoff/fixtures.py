"""Shared rolling-handoff test fixtures and loopback subprocess helpers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import cast
from typing import override

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.relay import admission as runtime_state_module
from openai_responses_proxy.serving import entrypoint as entrypoint_module
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles
from openai_responses_proxy.serving.handoff import transaction as handoff_module
from tests.lifecycle.fixtures import install_context
from tools.release import verification

ROOT = Path(__file__).resolve().parents[3]
PACKAGED_EXECUTABLE_START_TIMEOUT_SECONDS = 60


def expected_metadata(**overrides) -> dict[str, object]:
    """Return a complete protocol identity with optional field overrides."""
    identity: dict[str, object] = {
        "transaction_id": "txn-1",
        "release": "1.0.25",
        "serving_payload_sha256": "a" * 64,
        "release_receipt_sha256": "f" * 64,
        "manifest_sha256": "b" * 64,
    }
    identity.update(overrides)
    return identity


def child_message(kind: str, child, expected: dict[str, object]) -> dict[str, object]:
    """Return the exact parent-side READY, SERVING, or FINALIZED message."""
    message = {
        "type": kind,
        "pid": child.runtime_pid,
        "transaction_id": expected["transaction_id"],
    }
    if kind == "ready":
        message.update(
            protocol_version=handoff_module.HANDOFF_PROTOCOL_VERSION,
            release=expected["release"],
            serving_payload_sha256=expected["serving_payload_sha256"],
            release_receipt_sha256=expected["release_receipt_sha256"],
            manifest_sha256=expected["manifest_sha256"],
        )
    return message


def matching_health(child_or_pid, expected: dict[str, object], **overrides) -> dict[str, object]:
    """Return a complete child health identity with optional field overrides."""
    pid = child_or_pid if isinstance(child_or_pid, int) else child_or_pid.runtime_pid
    health = {
        "pid": pid,
        "handoff_protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
        "handoff_capabilities": [
            handoff_module.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_module.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ],
        "handoff_transaction_id": expected["transaction_id"],
        "release": expected["release"],
        "serving_payload_sha256": expected["serving_payload_sha256"],
        "release_receipt_sha256": expected["release_receipt_sha256"],
        "payload_manifest_sha256": expected["manifest_sha256"],
        "handoff_state": "serving",
        "accepting": True,
        "draining": False,
    }
    health.update(overrides)
    return health


def fake_child(*, pid: int = 54321, mocker):
    """Return a controllable parent-side child process fixture."""
    child = mocker.Mock()
    child.process = mocker.Mock(pid=pid)
    child.runtime_pid = pid
    child.terminate_bounded.return_value = True
    return child


def fake_server(*, port: int = 43123, mocker):
    """Return a parent-side listener fixture with a probeable address."""
    server = mocker.Mock()
    server.server_address = ("127.0.0.1", port)
    server.shutdown = mocker.Mock()
    return server


class Response:
    """Minimal context-managed JSON response fixture."""

    def __init__(self, payload, *, status: int = 202):
        """Initialize a context-managed JSON response fixture."""
        self.status = status
        self.payload = json.dumps(payload).encode() if isinstance(payload, dict) else payload

    def __enter__(self):
        """Return this response for a context-managed request."""
        return self

    def __exit__(self, *_args):
        """Propagate exceptions raised by the managed caller."""
        return False

    def read(self, _limit):
        return self.payload


def ready_ack(expected: dict[str, object], **overrides) -> dict[str, object]:
    """Return a protocol-v2 READY acknowledgement."""
    payload: dict[str, object] = {
        "ok": True,
        "state": "ready",
        "protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
        "transaction_id": expected["transaction_id"],
        "child_pid": 1000,
    }
    payload.update(overrides)
    return payload


def idle_runtime(**overrides) -> dict[str, object]:
    """Return a protocol-v2 runtime ready to initiate a handoff."""
    runtime: dict[str, object] = {
        "pid": 999,
        "handoff_protocol_version": handoff_module.HANDOFF_PROTOCOL_VERSION,
        "handoff_capabilities": [
            handoff_module.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_module.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ],
        "handoff_transaction_id": None,
        "handoff_state": "idle",
        "release": "1.0.24",
        "serving_payload_sha256": "a" * 64,
        "release_receipt_sha256": "e" * 64,
        "payload_manifest_sha256": "b" * 64,
        "accepting": True,
        "draining": False,
    }
    runtime.update(overrides)
    return runtime


def child_pid_matching_health(
    port: int,
    expected: dict[str, object],
    *,
    exclude_pid: int | None,
    states: frozenset[str] = frozenset(("serving", "finalized")),
):
    """Return the exact serving or finalized successor observed through health."""
    try:
        _, health = http_json(port, "/healthz", timeout=1)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    pid = health.get("pid")
    required = matching_health(pid, expected)
    required["handoff_state"] = health.get("handoff_state")
    return (
        pid
        if type(pid) is int
        and pid > 0
        and pid != exclude_pid
        and health.get("handoff_state") in states
        and all(health.get(key) == value for key, value in required.items())
        else None
    )


def child_pid_observer(
    port: int,
    expected: dict[str, object],
    *,
    exclude_pid: int | None,
    states: frozenset[str] = frozenset(("serving", "finalized")),
    owned_processes: dict[int, process.OwnedProcess] | None = None,
    executable: str | None = None,
) -> tuple[dict[str, int | None], Callable[[], bool]]:
    """Return a successor-PID cell for the accepted handoff states."""
    observed: dict[str, int | None] = {"value": None}

    def matches() -> bool:
        observed["value"] = child_pid_matching_health(
            port,
            expected,
            exclude_pid=exclude_pid,
            states=states,
        )
        if owned_processes is not None and observed["value"] is not None:
            if executable is None:
                raise ValueError("executable is required when capturing an owned process")
            owned = process.capture_generation(observed["value"], executable)
            if owned is None:
                observed["value"] = None
                return False
            owned_processes[owned.pid] = owned
        return observed["value"] is not None

    return observed, matches


def fake_handler(body: dict[str, object], *, mocker):
    """Return a loopback control-handler fixture containing one JSON body."""
    payload = json.dumps(body).encode()
    handler = mocker.Mock()
    handler.client_address = ("127.0.0.1", 51234)
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile.read.return_value = payload
    return handler


def free_port() -> int:
    """Return an unused loopback TCP port for an owned test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
        if not isinstance(address, tuple) or len(address) < 2 or not isinstance(address[1], int):
            raise RuntimeError("loopback socket did not expose an integer port")
        return address[1]


def wait_until(predicate: Callable[[], object], timeout: float, interval: float = 0.05) -> bool:
    """Poll a predicate until it becomes truthy or the bounded timeout expires."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def proxy_is_up(port: int) -> bool:
    """Return whether a loopback TCP listener currently accepts connections."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def http_json(port: int, path: str, *, method: str = "GET", body=None, timeout: float = 3.0):
    """Issue an unproxied loopback request and decode its JSON response."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return response.status, json.loads(response.read())


def terminate_process(process: subprocess.Popen[bytes], timeout: float = 5) -> None:
    """Terminate an owned subprocess and bound the kill fallback."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def terminate_owned_proxy(pid: int | None, proxy_script: str) -> None:
    """Terminate one child only while its argv names the temporary executable."""
    roles = {
        serving_roles.HANDOFF_CHILD_MODE,
        serving_roles.LISTENER_MODE,
    }
    if pid is None or not process.pid_names_executable(pid, proxy_script, roles=roles):
        return
    if process.terminate_executable(pid, proxy_script, roles=roles):
        return
    if process.pid_names_executable(pid, proxy_script, roles=roles):
        command = process.process_command(pid)
        raise RuntimeError(f"owned proxy child {pid} did not terminate: {command!r}")


def pid_alive(pid: int | None) -> bool:
    """Return whether an owned handoff child still has a process command."""
    return pid is not None and bool(process.process_command(pid))


type UpstreamBehavior = tuple[int, bytes] | Callable[[BaseHTTPRequestHandler], None]


class _DisconnectAwareHTTPServer(ThreadingHTTPServer):
    """Ignore only peer disconnects that an integration test intentionally causes."""

    @override
    def handle_error(self, request: object, client_address: object) -> None:
        error = sys.exc_info()[1]
        disconnects = {errno.ECONNABORTED, errno.ECONNRESET, errno.EPIPE}
        if isinstance(error, OSError) and error.errno in disconnects:
            return
        super().handle_error(
            cast("socket.socket | tuple[bytes, socket.socket]", request),
            cast("tuple[str, int] | str", client_address),
        )


class ScriptedUpstream:
    """Run a real loopback HTTP upstream with a deterministic response queue."""

    def __init__(self):
        """Initialize an empty deterministic upstream response queue."""
        self.received: list[bytes] = []
        received = self.received
        outer = self
        self._lock = threading.Lock()
        self._queue: list[UpstreamBehavior] = []

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received.append(self.rfile.read(length))
                with outer._lock:
                    behavior: UpstreamBehavior = (
                        outer._queue.pop(0) if outer._queue else (200, b'{"id":"ok"}')
                    )
                if not isinstance(behavior, tuple):
                    callback = cast("Callable[[BaseHTTPRequestHandler], None]", behavior)
                    callback(self)
                    return
                status, response_payload = cast("tuple[int, bytes]", behavior)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_payload)))
                self.end_headers()
                self.wfile.write(response_payload)

            def do_GET(self):
                self.do_POST()

        self.server = _DisconnectAwareHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        """Start serving after any subprocess that must avoid a threaded fork."""
        self.thread.start()

    def push(self, behavior: UpstreamBehavior) -> None:
        """Append one deterministic response behavior."""
        with self._lock:
            self._queue.append(behavior)

    def base_url(self) -> str:
        """Return the loopback base URL for this owned server."""
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        """Stop the server and join its owned thread."""
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)


def write_installed_payload(
    root: Path, *, release: str, port: int, upstream_url: str
) -> runtime_context.RuntimeContext:
    """Build one selector-bound temporary generation without touching source."""
    control = install_context(root, windows=os.name == "nt")
    ctx = generation.context(control, "0" * 32)
    ctx.port = port
    verification.retain_context(ctx)
    install_dir = Path(ctx.payload_dir)
    source = Path(os.environ["OPENAI_RESPONSES_PROXY_EXECUTABLE"])
    target = Path(ctx.executable)
    # Preserve executable modes, not host-local extended metadata. On macOS,
    # copy2 propagates provenance attributes to every frozen-runtime file and
    # turns each isolated test copy into a fresh Gatekeeper scan. Published
    # archives do not carry those checkout-local attributes.
    shutil.copytree(source.parent, target.parent, copy_function=shutil.copy)
    provider_manifest = install_dir / inventory.PROVIDER_MANIFEST
    provider_manifest.write_text(
        f'version = 1\n\n[providers.dmxapi]\nbase_url = "{upstream_url}"\npolicy = "dmxapi"\n',
        encoding="utf-8",
    )
    receipt = install_dir / inventory.RELEASE_RECEIPT_FILENAME
    receipt.write_bytes(b'{"fixture":"handoff-subprocess"}\n')
    files = [provider_manifest, *(path for path in target.parent.rglob("*") if path.is_file())]
    manifest = projection.manifest_for_digests(
        release,
        {
            path.relative_to(install_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        },
        hashlib.sha256(receipt.read_bytes()).hexdigest(),
    )
    projection.payload_manifest_path(ctx).write_bytes(projection.manifest_bytes(manifest))
    runtime_spec.write(ctx)
    generation.select(control, active="0" * 32, predecessor=None)
    # Exercise the same installed-candidate admission as the product. A copied
    # frozen macOS payload can incur its one-time trust-cache startup before
    # any application log exists; paying that bounded cost here keeps the
    # handoff tests focused on listener transfer rather than host assessment.
    from openai_responses_proxy.lifecycle import candidate as payload_candidate

    try:
        payload_candidate.prewarm(ctx)
    except errors.InstallError as exc:
        # Native acceptance owns diagnostics; public lifecycle output must not
        # render subprocess bytes. Keep the actual failure for hosted diagnosis.
        cause = exc.__cause__
        if isinstance(cause, subprocess.CalledProcessError):
            print(f"native candidate prewarm exit: {cause.returncode}")
            for stream in (cause.stdout, cause.stderr):
                if stream:
                    print(stream.decode("utf-8", errors="replace"))
        raise
    return ctx


def installed_expected_metadata(
    ctx: runtime_context.RuntimeContext,
    transaction_id: str,
) -> dict[str, object]:
    """Read the exact identity expected from a prepared child runtime."""
    manifest_path = Path(projection.payload_manifest_path(ctx))
    manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict), "fixture payload manifest must be an object"
    return {
        "transaction_id": transaction_id,
        "release": manifest["release"],
        "serving_payload_sha256": manifest["serving_payload_sha256"],
        "release_receipt_sha256": manifest.get("release_receipt_sha256", "e" * 64),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "timeout_seconds": 30,
    }


def start_real_proxy(
    ctx: runtime_context.RuntimeContext,
    *,
    upstream_url: str,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start an installed-like proxy and prove its listener became reachable."""
    parsed_upstream = urllib.parse.urlsplit(upstream_url)
    try:
        upstream_port = parsed_upstream.port
    except ValueError:
        upstream_port = None
    if (
        parsed_upstream.scheme != "http"
        or parsed_upstream.hostname != "127.0.0.1"
        or upstream_port is None
        or parsed_upstream.path not in ("", "/")
        or parsed_upstream.query
        or parsed_upstream.fragment
        or parsed_upstream.username is not None
        or parsed_upstream.password is not None
    ):
        raise ValueError("real proxy tests require an owned loopback HTTP upstream")

    provider_manifest = Path(ctx.payload_dir) / inventory.PROVIDER_MANIFEST
    expected_manifest = (
        f'version = 1\n\n[providers.dmxapi]\nbase_url = "{upstream_url}"\npolicy = "dmxapi"\n'
    )
    if provider_manifest.read_text(encoding="utf-8") != expected_manifest:
        raise ValueError("installed provider manifest does not match the owned test upstream")
    env = dict(os.environ)
    env["OPENAI_RESPONSES_PROXY_PROXY_HOST"] = "127.0.0.1"
    env["OPENAI_RESPONSES_PROXY_PROXY_PORT"] = str(ctx.port)
    env["OPENAI_RESPONSES_PROXY_PROXY_LOG"] = str(log_path)
    # The temporary payload must not inherit a live test host's product state.
    # Without this boundary, a listener can resume a stale handoff transaction
    # from the operator's installed runtime instead of serving the fixture.
    env["OPENAI_RESPONSES_PROXY_HOME"] = ctx.install_dir
    env["OPENAI_RESPONSES_PROXY_STATE_HOME"] = str(log_path.parent / "state")
    env["OPENAI_RESPONSES_PROXY_EXECUTABLE"] = ctx.executable
    env.pop("PYTHONPATH", None)
    if extra_env:
        env.update(extra_env)
    with log_path.open("ab") as diagnostic:
        process = subprocess.Popen(
            [ctx.executable, "--internal-listener"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=diagnostic,
            close_fds=True,
        )
    # Keep native acceptance bounded while allowing for a cold hosted-runner
    # start under contention.
    if not wait_until(
        lambda: process.poll() is not None or proxy_is_up(ctx.port),
        timeout=PACKAGED_EXECUTABLE_START_TIMEOUT_SECONDS,
    ):
        terminate_process(process)
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:].strip()
        raise RuntimeError(
            "real proxy subprocess did not bind its listening socket in time"
            + (f": {detail}" if detail else "")
        )
    if process.poll() is not None:
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:].strip()
        raise RuntimeError(
            "real proxy subprocess exited before binding its listening socket"
            + (f": {detail}" if detail else "")
        )
    return process


def handoff_outcome_ready() -> threading.Event:
    """Return the current handoff session's typed completion event."""
    event = handoff_module._HANDOFF_SESSION["outcome_ready"]
    assert isinstance(event, threading.Event)
    return event


class HandoffFixture:
    """Reset handoff and runtime state for parent-side unit tests."""

    def setup_method(self):
        self.p = handoff_module
        runtime_state_module.reset_for_test()
        self.p.reset_session_to_idle()
        self.log_events: list[str] = []
        self.context = entrypoint_module._handoff_context()
        object.__setattr__(
            self.context,
            "successor_executable",
            lambda: self.context.executable,
        )
        object.__setattr__(self.context, "log", self.log_events.append)
        self.installation = runtime_state_module
