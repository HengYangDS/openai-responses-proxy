"""Measure deterministic proxy hot paths without third-party network latency."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import subprocess
import threading
import time
import urllib.request
from collections.abc import Generator
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import override

import psutil
import pyperf

from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import process as process_owner
from openai_responses_proxy.lifecycle.supervision import watchdog
from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.serving import entrypoint
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles
from openai_responses_proxy.serving.handoff.protocol import read_control_message
from openai_responses_proxy.serving.handoff.protocol import write_control_message

_SMALL_RESPONSE = b'{"id":"response","status":"completed"}'
_STREAM_RESPONSE = b'data: {"type":"response.completed"}\n\n'
_STREAM_FIRST_EVENTS = (
    b'data: {"type":"response.created"}\n\n'
    b'data: {"type":"response.output_text.delta","delta":"first"}\n\n'
)
# Bounds how long the upstream withholds its terminal event; a relay that waited
# for upstream completion would deliver its first event only after this.
_FIRST_EVENT_HOLD_SECONDS = 5.0
# Bounds one packaged launch; a cold host can need seconds before it binds.
_STARTUP_DEADLINE_SECONDS = 30.0
_LARGE_RESPONSE = json.dumps(
    {"id": "response", "status": "completed", "output": [{"text": "x" * (1024 * 1024)}]},
    separators=(",", ":"),
).encode()
_REQUEST_BODY = json.dumps(
    {
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "x" * 4096}],
            }
        ]
    },
    separators=(",", ":"),
).encode()
_LARGE_REQUEST_BODY = json.dumps(
    {"input": [{"type": "message", "role": "user", "content": "x" * (1024 * 1024)}]},
    separators=(",", ":"),
).encode()


class _UpstreamHandler(BaseHTTPRequestHandler):
    """Serve deterministic benchmark responses on loopback."""

    protocol_version = "HTTP/1.1"
    first_event_read = threading.Event()
    terminals_without_first_event = 0

    @override
    def log_message(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path.endswith("?stream=first"):
            self._stream_after_first_event()
            return
        if self.path.endswith("?stream=1"):
            payload, content_type = _STREAM_RESPONSE, "text/event-stream"
        elif self.path.endswith("?large=1"):
            payload, content_type = _LARGE_RESPONSE, "application/octet-stream"
        else:
            payload, content_type = _SMALL_RESPONSE, "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _stream_after_first_event(self) -> None:
        """Withhold the terminal event until the client has read the first event."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(_STREAM_FIRST_EVENTS) + len(_STREAM_RESPONSE)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(_STREAM_FIRST_EVENTS)
        if not _UpstreamHandler.first_event_read.wait(_FIRST_EVENT_HOLD_SECONDS):
            _UpstreamHandler.terminals_without_first_event += 1
        _UpstreamHandler.first_event_read.clear()
        self.wfile.write(_STREAM_RESPONSE)


class _Environment:
    """Own the loopback upstream and proxy used by one benchmark process."""

    def __init__(self) -> None:
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        upstream_url = f"http://127.0.0.1:{self.upstream.server_address[1]}"
        self.registry = provider_registry.Registry(
            {"dmxapi": provider_registry.Profile("dmxapi", upstream_url)}
        )
        self.proxy = entrypoint.create_server(("127.0.0.1", 0), providers=self.registry)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.proxy_thread = threading.Thread(
            target=self.proxy.serve_forever,
            kwargs={"poll_interval": 0.001},
            daemon=True,
        )
        self.upstream_thread.start()
        self.proxy_thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def close(self) -> None:
        """Stop both owned listeners and join their threads."""
        self.proxy.shutdown()
        self.proxy.server_close()
        self.proxy_thread.join()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def exchange(self, query: str = "") -> bytes:
        """Return one complete loopback response."""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.proxy.server_address[1]}/dmxapi/v1/responses{query}",
            data=_REQUEST_BODY,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            content: object = response.read()
            if not isinstance(content, bytes):
                raise TypeError("benchmark response must be bytes")
            return content

    def first_event(self, loops: int) -> float:
        """Return the time ``loops`` streams take to deliver their first event.

        The clock stops when the first complete event arrives; the rest of each
        stream is read afterwards, outside the measurement.
        """
        elapsed = 0.0
        for _ in range(loops):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.proxy.server_address[1], timeout=10
            )
            try:
                started = pyperf.perf_counter()
                connection.request(
                    "POST",
                    "/dmxapi/v1/responses?stream=first",
                    body=_REQUEST_BODY,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                received = b""
                while b"\n\n" not in received:
                    chunk = response.read1(65536)
                    if not chunk:
                        raise RuntimeError("benchmark stream ended before its first event")
                    received += chunk
                elapsed += pyperf.perf_counter() - started
                _UpstreamHandler.first_event_read.set()
                response.read()
            finally:
                connection.close()
        return elapsed

    def health(self) -> bytes:
        """Return one process-local health response."""
        with self.opener.open(
            f"http://127.0.0.1:{self.proxy.server_address[1]}/healthz"
        ) as response:
            content: object = response.read()
            if not isinstance(content, bytes):
                raise TypeError("benchmark health response must be bytes")
            return content


@contextlib.contextmanager
def _environment() -> Generator[_Environment]:
    environment = _Environment()
    try:
        yield environment
    finally:
        environment.close()


@dataclass(frozen=True, slots=True)
class _PackagedListener:
    """The private packaged generation that startup-to-ready launches."""

    executable: Path
    port: int
    expected: Mapping[str, str]


def _private_listener() -> _PackagedListener:
    """Resolve the session's private selected generation as the product does.

    Only an explicit private installation is admitted, so the benchmark can never
    launch a listener of the operator's installed product.
    """
    home = os.environ.get(runtime_config.HOME_ENV, "")
    if not os.path.isabs(home):
        raise RuntimeError(
            "startup-to-ready requires the performance session's private installation"
        )
    root = Path(home)
    selection = identity.read_payload_selection(root / identity.PAYLOAD_SELECTOR_FILENAME)
    executable = Path(
        root,
        identity.PAYLOAD_GENERATIONS_DIRNAME,
        selection.active,
        inventory.executable_name(windows=os.name == "nt"),
    ).resolve(strict=True)
    committed = identity.committed_payload(executable)
    if committed is None:
        raise RuntimeError("the private packaged generation is not a committed payload")
    settings = runtime_spec.environment(runtime_spec.from_executable(executable))
    return _PackagedListener(
        executable, int(settings[runtime_config.PROXY_PORT_ENV]), committed.handoff()
    )


def _startup_to_ready(loops: int) -> float:
    """Return the time ``loops`` packaged listener launches take to serve health."""
    listener = _private_listener()
    return sum(_observe_startup(listener) for _ in range(loops))


def _observe_startup(listener: _PackagedListener) -> float:
    """Time one launch from process creation through its first complete health body.

    The listener starts exactly as the watchdog starts it. Identity checks and
    teardown run after the clock stops, and every launched process is retired.
    """
    started = pyperf.perf_counter()
    child = watchdog.spawn_proxy(str(listener.executable))
    if child is None:
        raise RuntimeError("the packaged listener did not start")
    try:
        body = _first_health(child, listener.port)
        elapsed = pyperf.perf_counter() - started
        health: object = json.loads(body)
        if (
            not isinstance(health, dict)
            or not identity.runtime_payload_matches(health, listener.expected)
            or health.get("pid") != child.pid
        ):
            raise RuntimeError("health came from another listener or payload")
        return elapsed
    finally:
        _retire(child, listener.executable)


def _first_health(child: subprocess.Popen[bytes], port: int) -> bytes:
    """Return the first complete successful health body of one launched listener."""
    deadline = time.monotonic() + _STARTUP_DEADLINE_SECONDS
    while True:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.05)
        try:
            connection.request("GET", "/healthz", headers={"Connection": "close"})
            response = connection.getresponse()
            body = response.read()
            if response.status == 200:
                return body
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if child.poll() is not None:
            raise RuntimeError("the packaged listener exited before serving health")
        if time.monotonic() >= deadline:
            raise TimeoutError("the packaged listener did not serve health before its deadline")
        time.sleep(0.001)


def _retire(child: subprocess.Popen[bytes], executable: Path) -> None:
    """Retire one launched listener and its descendants through the process owner.

    A process the owner cannot capture, or whose termination it cannot prove, is
    stopped through its launch handle and reported, so cleanup failure cannot
    pass as a measurement and no listener outlives a failed sample.
    """
    captured: list[process_owner.OwnedProcess] = []
    if child.poll() is None:
        descendants = [child.pid]
        with contextlib.suppress(psutil.Error):
            descendants += [item.pid for item in psutil.Process(child.pid).children(recursive=True)]
        for pid in descendants:
            owned = process_owner.capture_executable(
                pid, str(executable), roles={roles.LISTENER_MODE}
            )
            if owned is not None:
                captured.append(owned)
        if not any(owned.pid == child.pid for owned in captured):
            child.kill()
            child.wait(timeout=5)
            raise RuntimeError(
                "packaged listener ownership is unproved; its launch handle stopped it"
            )
    try:
        for owned in reversed(captured):
            process_owner.terminate_owned_process(owned, timeout_seconds=5.0)
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    if any(process_owner.owned_process_alive(owned) for owned in captured):
        raise RuntimeError("a packaged listener process remains after teardown")


def _handoff_round_trip() -> dict[str, object]:
    message = {"phase": "ready", "pid": 1234, "release": "2.0.56"}
    stream = io.BytesIO()
    write_control_message(stream, message)
    stream.seek(0)
    return read_control_message(stream)


def main() -> None:
    """Run repeated benchmarks and let pyperf own process isolation and JSON."""
    runner = pyperf.Runner(program_args=("-m", "tools.performance.benchmark"))
    registry = provider_registry.Registry(
        {"dmxapi": provider_registry.Profile("dmxapi", "https://example.invalid/v1")}
    )
    runner.bench_func("provider-route-resolution", registry.resolve, "/dmxapi/v1/responses")
    runner.bench_func("responses-projection-4kib", sanitize_responses_body, _REQUEST_BODY)
    runner.bench_time_func("startup-to-ready", _startup_to_ready)
    runner.bench_func("handoff-control-round-trip", _handoff_round_trip)
    with _environment() as environment:
        runner.bench_func("health-status", environment.health)
        runner.bench_func("non-streaming-request", environment.exchange)
        runner.bench_time_func("streaming-first-event", environment.first_event)
        runner.bench_func("forwarding-1mib", environment.exchange, "?large=1")


def memory_main() -> None:
    """Measure peak process memory for the largest pure request projection."""
    runner = pyperf.Runner(program_args=("-m", "tools.performance.memory"))
    runner.bench_func("large-request-projection-1mib", sanitize_responses_body, _LARGE_REQUEST_BODY)
    with _environment() as environment:
        runner.bench_func("large-response-forwarding-1mib", environment.exchange, "?large=1")


if __name__ == "__main__":
    main()
