"""Measure deterministic proxy hot paths without third-party network latency."""

from __future__ import annotations

import contextlib
import io
import json
import threading
import urllib.request
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import override

import pyperf

from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.serving import entrypoint
from openai_responses_proxy.serving.handoff.protocol import read_control_message
from openai_responses_proxy.serving.handoff.protocol import write_control_message

_SMALL_RESPONSE = b'{"id":"response","status":"completed"}'
_STREAM_RESPONSE = b'data: {"type":"response.completed"}\n\n'
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

    @override
    def log_message(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
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


def _startup_to_ready() -> None:
    registry = provider_registry.Registry(
        {"dmxapi": provider_registry.Profile("dmxapi", "https://example.invalid/v1")}
    )
    server = entrypoint.create_server(("127.0.0.1", 0), providers=registry)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.001},
        daemon=True,
    )
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{server.server_address[1]}/healthz") as response:
            response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
    runner.bench_func("startup-to-ready", _startup_to_ready)
    runner.bench_func("handoff-control-round-trip", _handoff_round_trip)
    with _environment() as environment:
        runner.bench_func("health-status", environment.health)
        runner.bench_func("non-streaming-request", environment.exchange)
        runner.bench_func("streaming-first-event", environment.exchange, "?stream=1")
        runner.bench_func("forwarding-1mib", environment.exchange, "?large=1")


def memory_main() -> None:
    """Measure peak process memory for the largest pure request projection."""
    runner = pyperf.Runner(program_args=("-m", "tools.performance.memory"))
    runner.bench_func("large-request-projection-1mib", sanitize_responses_body, _LARGE_REQUEST_BODY)
    with _environment() as environment:
        runner.bench_func("large-response-forwarding-1mib", environment.exchange, "?large=1")


if __name__ == "__main__":
    main()
