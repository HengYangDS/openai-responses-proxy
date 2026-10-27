"""Packaged startup-to-ready times one owned launch through its first health body."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import roles
from tools.performance import benchmark
from tools.release import verification

# A stand-in listener: it waits before binding, answers its first health
# requests as still starting, then serves one identity on /healthz. It keeps
# the default SIGTERM action, so its teardown does not depend on how quickly an
# interpreter runs a Python-level signal handler.
_LISTENER = r"""
import http.server, json, os, sys, time
port, bind_delay, served, starting = int(sys.argv[2]), float(sys.argv[3]), json.loads(sys.argv[4]), int(sys.argv[5])
if bind_delay < 0:
    sys.exit(3)
time.sleep(bind_delay)
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global starting
        status, body = 200, json.dumps({"pid": os.getpid(), **served}).encode()
        if starting > 0:
            starting -= 1
            status, body = 503, b'{"status":"starting"}'
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", port), Health).serve_forever()
"""
_IDENTITY = {
    "release": "9.9.9",
    "serving_payload_sha256": "a" * 64,
    "release_receipt_sha256": "b" * 64,
    "manifest_sha256": "c" * 64,
}
# The stand-in runs the interpreter image that this process runs, as the
# watchdog runs the declared executable itself, so the launched process is the
# one that serves health and that the process owner identifies. Started through
# sys.executable it is not: a Windows virtual environment's launcher runs the
# interpreter as its child, and on GitHub's macOS runners a stand-in started
# through the framework build's stub never served health.
_INTERPRETER = Path(psutil.Process().exe())
_LAUNCHED: list[subprocess.Popen[bytes]] = []


@pytest.fixture(autouse=True)
def _stop_stand_ins() -> Iterator[None]:
    """Stop every stand-in a test left running, after its assertions."""
    yield
    while _LAUNCHED:
        child = _LAUNCHED.pop()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def _served(**overrides: object) -> dict[str, object]:
    """Return the health fields a listener of the declared identity serves."""
    runtime: dict[str, object] = {
        ("payload_manifest_sha256" if name == "manifest_sha256" else name): value
        for name, value in _IDENTITY.items()
    }
    runtime.update(overrides)
    return runtime


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _launches(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bind_delay: float = 0.0,
    starting: int = 0,
    served: dict[str, object] | None = None,
    executable: Path | None = None,
) -> tuple[benchmark._PackagedListener, list[subprocess.Popen[bytes]]]:
    """Return a listener whose launches start the stand-in as the watchdog would."""
    port = _free_port()
    children: list[subprocess.Popen[bytes]] = []

    def spawn(_selected: str) -> subprocess.Popen[bytes]:
        child = subprocess.Popen(
            [
                str(_INTERPRETER),
                "-c",
                _LISTENER,
                roles.LISTENER_MODE,
                str(port),
                str(bind_delay),
                json.dumps(served or _served()),
                str(starting),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        children.append(child)
        _LAUNCHED.append(child)
        return child

    monkeypatch.setattr(benchmark.watchdog, "spawn_proxy", spawn)
    return benchmark._PackagedListener(executable or _INTERPRETER, port, dict(_IDENTITY)), children


def _gone(child: subprocess.Popen[bytes]) -> bool:
    return child.returncode is not None and not psutil.pid_exists(child.pid)


def test_startup_stops_at_the_first_complete_health_body(monkeypatch: pytest.MonkeyPatch) -> None:
    # The stand-in binds after 0.3 s, and its teardown takes 0.8 s longer than
    # usual: the clock must include the first and exclude the second.
    listener, children = _launches(monkeypatch, bind_delay=0.3)
    retire = benchmark._retire

    def slow_retire(child: subprocess.Popen[bytes], executable: Path) -> None:
        time.sleep(0.8)
        retire(child, executable)

    monkeypatch.setattr(benchmark, "_retire", slow_retire)
    started = time.monotonic()
    elapsed = benchmark._observe_startup(listener)
    total = time.monotonic() - started
    assert elapsed >= 0.3, "the clock must cover startup"
    assert total - elapsed >= 0.8, "the clock must exclude teardown"
    (child,) = children
    assert _gone(child), "every launched listener must be proven gone"


@pytest.mark.parametrize(
    ("served", "problem"),
    [
        (_served(release="0.0.1"), "another listener or payload"),
        (_served(pid=1), "another listener or payload"),
    ],
)
def test_startup_refuses_health_from_another_identity(
    served: dict[str, object], problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener, children = _launches(monkeypatch, served=served)
    with pytest.raises(RuntimeError, match=problem):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_reports_a_listener_that_exits_before_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener, children = _launches(monkeypatch, bind_delay=-1)
    with pytest.raises(RuntimeError, match="exited before"):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_has_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "_STARTUP_DEADLINE_SECONDS", 0.3)
    listener, children = _launches(monkeypatch, bind_delay=30)
    with pytest.raises(TimeoutError):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_refuses_a_launch_that_did_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark.watchdog, "spawn_proxy", lambda _selected: None)
    with pytest.raises(RuntimeError, match="did not start"):
        benchmark._observe_startup(
            benchmark._PackagedListener(Path(sys.executable), _free_port(), dict(_IDENTITY))
        )


def test_startup_proves_ownership_before_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The launched process does not run the declared executable, so the native
    # process owner cannot capture it; the launch handle still retires it.
    listener, children = _launches(
        monkeypatch, executable=tmp_path / "bin" / "openai-responses-proxy"
    )
    with pytest.raises(RuntimeError, match="ownership"):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_waits_through_unsuccessful_health(monkeypatch: pytest.MonkeyPatch) -> None:
    # A listener that still answers 503 is starting; accepting that body as its
    # first health would fail the identity check.
    listener, children = _launches(monkeypatch, starting=3)
    benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_refuses_a_listener_that_survives_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The process owner's liveness proof, not the launch handle, decides whether
    # teardown completed; a process it still reports alive fails the sample.
    listener, children = _launches(monkeypatch)
    monkeypatch.setattr(benchmark.process_owner, "owned_process_alive", lambda _owned: True)
    with pytest.raises(RuntimeError, match="remains after teardown"):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_stops_a_listener_whose_termination_is_unproved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A termination the owner cannot prove fails the sample, and the launch
    # handle still stops the listener, so no process outlives a failed sample.
    listener, children = _launches(monkeypatch)

    def unproved(*_arguments: object, **_options: object) -> bool:
        raise errors.InstallError("exit is unproved")

    monkeypatch.setattr(benchmark.process_owner, "terminate_owned_process", unproved)
    with pytest.raises(errors.InstallError, match="unproved"):
        benchmark._observe_startup(listener)
    assert all(_gone(child) for child in children)


def test_startup_requires_the_session_private_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runtime_config.HOME_ENV, raising=False)
    with pytest.raises(RuntimeError, match="private installation"):
        benchmark._private_listener()
    monkeypatch.setenv(runtime_config.HOME_ENV, "relative/home")
    with pytest.raises(RuntimeError, match="private installation"):
        benchmark._private_listener()


def _private_generation(root: Path, port: int) -> runtime_context.RuntimeContext:
    """Install a fixture bundle as the selected private generation under root."""
    bundle = root / "bundle"
    bundle.mkdir()
    source = bundle / product_identity.executable_name(windows=os.name == "nt")
    source.write_bytes(b"native fixture")
    (bundle / "_internal").mkdir()
    (bundle / "_internal" / "library.zip").write_bytes(b"library fixture")
    return verification.install_private_generation(
        verification.private_installation(root / "payload", root / "state"),
        source,
        port=port,
        release="9.9.9",
        providers='version = 1\n\n[providers.dmxapi]\nbase_url = "https://example.invalid/v1"\npolicy = "dmxapi"\n',
        receipt=b'{"fixture":"startup"}\n',
    )


def test_startup_resolves_the_selected_private_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = _free_port()
    ctx = _private_generation(tmp_path, port)
    monkeypatch.setenv(runtime_config.HOME_ENV, ctx.install_dir)
    listener = benchmark._private_listener()
    committed = identity.committed_payload(Path(ctx.executable))
    assert committed is not None
    assert listener == benchmark._PackagedListener(
        Path(ctx.executable).resolve(), port, committed.handoff()
    )
    assert committed.release == "9.9.9"
    assert verification.private_processes(ctx) == []


def test_startup_refuses_a_generation_whose_payload_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _private_generation(tmp_path, _free_port())
    Path(ctx.executable).write_bytes(b"changed native fixture")
    monkeypatch.setenv(runtime_config.HOME_ENV, ctx.install_dir)
    with pytest.raises(RuntimeError, match="not a committed payload"):
        benchmark._private_listener()


def test_startup_times_one_launch_per_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = benchmark._PackagedListener(Path(sys.executable), 1, dict(_IDENTITY))
    launches: list[benchmark._PackagedListener] = []
    monkeypatch.setattr(benchmark, "_private_listener", lambda: listener)
    monkeypatch.setattr(
        benchmark, "_observe_startup", lambda selected: launches.append(selected) or 0.25
    )
    assert benchmark._startup_to_ready(3) == pytest.approx(0.75)
    assert launches == [listener] * 3
