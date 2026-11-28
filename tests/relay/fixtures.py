"""Loopback proxy and direct transport fixtures shared by relay contracts."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from email.message import Message
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import cast
from typing import override

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.relay import upstream as upstream_transport
from openai_responses_proxy.serving import entrypoint as proxy

if TYPE_CHECKING:
    from collections.abc import Generator

ScriptedResponse = tuple[int, bytes] | Mapping[str, object]

# The port of the scripted upstream that serve_proxy started last; direct tests with no
# server address an upstream that nothing listens on.
_UPSTREAM_PORT = [9]

# The scripted upstream's own Server and Date, which differ from any the proxy writes.
UPSTREAM_SERVER = "upstream-fixture/1"
UPSTREAM_DATE = "Sat, 01 Jan 2000 00:00:00 GMT"


def upstream_path(
    resource: str = "responses",
    *,
    scheme: str = "http",
    host: str = "127.0.0.1",
    base: str = "/v1",
    query: str = "",
) -> str:
    """Return a proxy path that names the scripted upstream, as a client's base URL does."""
    suffix = f"?{query}" if query else ""
    return f"/{scheme}/{host}:{_UPSTREAM_PORT[0]}{base}/{resource}{suffix}"


def proxy_path(base_url: str, resource: str = "responses") -> str:
    """Return the proxy path for a client base URL, as the control plane writes it."""
    return "/" + base_url.replace("://", "/", 1) + "/" + resource


def upstream_origin(*, scheme: str = "http", host: str = "127.0.0.1", base: str = "/v1") -> str:
    """Return the origin that upstream_path names: the key of its cooldowns and log lines."""
    return f"{scheme}://{host}:{_UPSTREAM_PORT[0]}{base}"


EXACT_ERROR = json.dumps(
    {
        "error": {
            "message": (
                "invalid request body: Invalid 'input': value did not match any expected variant"
            ),
            "type": "invalid_request_error",
            "param": "",
            "code": "validation_error",
        }
    },
    separators=(",", ":"),
).encode()


def serve_proxy(
    responses: Sequence[ScriptedResponse],
    log_dir: str | Path,
    *,
    captures: list[dict[str, object]] | None = None,
) -> tuple[int, list[bytes], Callable[[], None]]:
    """Start scripted loopback servers and return port, bodies, and cleanup."""
    scripted = list(responses)
    received: list[bytes] = []
    scripted_lock = threading.Lock()

    class UpstreamHandler(BaseHTTPRequestHandler):
        @override
        def log_message(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        @override
        def version_string(self) -> str:
            return UPSTREAM_SERVER

        @override
        def date_time_string(self, timestamp: float | None = None) -> str:
            del timestamp
            return UPSTREAM_DATE

        def _receive(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append(body)
            if captures is not None:
                captures.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "body": body,
                    }
                )
            return body

        def _reply(self) -> None:
            with scripted_lock:
                response = scripted.pop(0)
            if isinstance(response, Mapping):
                status = response.get("status", 200)
                assert isinstance(status, int)
                assert not isinstance(status, bool)
                payload = response.get("payload")
                raw_chunks = response.get("chunks", [payload] if isinstance(payload, bytes) else [])
                assert isinstance(raw_chunks, list)
                assert all(isinstance(chunk, bytes) for chunk in raw_chunks)
                chunks = [chunk for chunk in raw_chunks if isinstance(chunk, bytes)]
                started = response.get("started_event")
                if started is not None:
                    assert isinstance(started, threading.Event)
                    started.set()
                release = response.get("release_event")
                if release is not None:
                    assert isinstance(release, threading.Event)
                    release.wait(timeout=5)
                stall = response.get("stall_event")
                assert stall is None or isinstance(stall, threading.Event)
                content_type = response.get("content_type", "text/event-stream")
                assert isinstance(content_type, str)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                if stall is None:
                    self.send_header("Connection", "close")
                else:
                    # Chunked framing lets a held-open stream deliver events
                    # incrementally, the way a real SSE upstream does.
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for chunk in chunks:
                    framed = b"%X\r\n%s\r\n" % (len(chunk), chunk) if stall is not None else chunk
                    self.wfile.write(framed)
                    self.wfile.flush()
                if stall is not None:
                    stall.wait(timeout=10)
                    self.wfile.write(b"0\r\n\r\n")
                self.close_connection = True
                return
            status, payload = response
            assert isinstance(status, int)
            assert isinstance(payload, bytes)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            self._receive()
            self._reply()

        def do_GET(self) -> None:
            self._receive()
            self._reply()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream.daemon_threads = False
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    previous_upstream_port = _UPSTREAM_PORT[0]
    _UPSTREAM_PORT[0] = upstream.server_address[1]
    old_log_path = operational_log.LOG_PATH
    operational_log.LOG_PATH = str(Path(log_dir) / "proxy.log")
    server = proxy.create_server(("127.0.0.1", 0))
    server.daemon_threads = False
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def cleanup() -> None:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
        operational_log.LOG_PATH = old_log_path
        _UPSTREAM_PORT[0] = previous_upstream_port

    return server.server_address[1], received, cleanup


@contextlib.contextmanager
def running_proxy(
    responses: Sequence[ScriptedResponse],
    *,
    captures: list[dict[str, object]] | None = None,
) -> Generator[tuple[int, list[bytes]]]:
    """Yield a scripted loopback proxy and always release both servers."""
    with tempfile.TemporaryDirectory() as log_dir:
        port, received, cleanup = serve_proxy(
            responses,
            log_dir,
            captures=captures,
        )
        try:
            yield port, received
        finally:
            cleanup()


def request(
    proxy_port: int,
    body: bytes = b"",
    *,
    path: str | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> http.client.HTTPResponse:
    """Send one loopback request without host proxies."""
    request_headers = {"Content-Type": "application/json"}
    if headers is not None:
        request_headers.update(headers)
    outbound = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}{upstream_path() if path is None else path}",
        data=body or None,
        method=method,
        headers=request_headers,
    )
    response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(outbound)
    if not isinstance(response, http.client.HTTPResponse):
        raise TypeError("loopback request did not return an HTTP response")
    return response


def raw_exchange(proxy_port: int, payload: bytes, *, timeout: float = 5.0) -> bytes:
    """Write one raw byte stream to the listener and read until it closes."""
    received = bytearray()
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout) as client:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            client.sendall(payload)
        while True:
            try:
                chunk = client.recv(65536)
            except (ConnectionResetError, TimeoutError):
                break
            if not chunk:
                break
            received.extend(chunk)
    return bytes(received)


def json_body(payload: object) -> bytes:
    """Encode one JSON request or response body compactly."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def request_body(*, stream: bool = False, secret: str = "private-current-prompt") -> bytes:
    """Return a current-turn request carrying removable provider bindings."""
    return json.dumps(
        {
            "model": "gpt-5.6-terra",
            "stream": stream,
            "instructions": "top-level-current-policy",
            "previous_response_id": "stale-response-binding",
            "conversation": {"id": "stale-conversation-binding"},
            "prompt_cache_key": "stale-private-cache-key",
            "include": ["reasoning.encrypted_content", "other"],
            "input": [
                {"type": "message", "role": "developer", "content": "current policy"},
                {
                    "type": "custom_tool_call",
                    "call_id": "stale-call",
                    "name": "exec",
                    "input": "{}",
                },
                {"type": "message", "role": "user", "content": secret},
            ],
        },
        separators=(",", ":"),
    ).encode()


class _MemoryConnection(socket.socket):
    """Model timeout selection without allocating a native socket descriptor."""

    def __init__(self) -> None:
        self._memory_timeout: float | None = None

    @override
    def gettimeout(self) -> float | None:
        return self._memory_timeout

    @override
    def settimeout(self, value: float | None) -> None:
        self._memory_timeout = value


class MemoryHandler(BaseHTTPRequestHandler):
    """Small handler surface for direct transport branch contracts."""

    def __init__(self, body: bytes = b"", *, path: str | None = None) -> None:
        """Initialize one in-memory downstream HTTP exchange."""
        self.path = upstream_path() if path is None else path
        # A native local client: a loopback peer that names a loopback Host.
        self.client_address = ("127.0.0.1", 50000)
        self.headers = Message()
        self.headers["Host"] = "127.0.0.1:8792"
        self.headers["Content-Length"] = str(len(body))
        self.headers["Connection"] = "close"
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.connection = _MemoryConnection()
        self.statuses: list[int] = []
        self.sent_headers: list[tuple[str, str]] = []

    @override
    def send_response(self, code: int, message: str | None = None) -> None:
        del message
        self.statuses.append(code)

    @override
    def send_response_only(self, code: int, message: str | None = None) -> None:
        del message
        self.statuses.append(code)

    @override
    def send_header(self, keyword: str, value: str) -> None:
        self.sent_headers.append((keyword, value))

    @override
    def end_headers(self) -> None:
        pass

    def output(self) -> bytes:
        """Return the bytes written through the in-memory response stream."""
        return cast("io.BytesIO", self.wfile).getvalue()


def http_error(code: int, message: str, body: bytes) -> urllib.error.HTTPError:
    """Return an HTTP error with the stdlib header contract."""
    return urllib.error.HTTPError(
        "https://upstream.test/v1/responses", code, message, Message(), io.BytesIO(body)
    )


class DirectResponse:
    """Scripted response supporting normal reads and read exceptions."""

    def __init__(
        self,
        *reads: bytes | BaseException,
        content_type: str = "application/json",
        status: int = 200,
        fp: object | None = None,
    ) -> None:
        """Initialize one deterministic upstream response sequence."""
        self._reads = list(reads)
        self.headers = {"Content-Type": content_type, "Content-Length": "opaque"}
        self.status = status
        self.fp = fp if fp is not None else object()

    def read(self, amount: int = -1) -> bytes:
        del amount
        item = self._reads.pop(0) if self._reads else b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        """Satisfy the upstream response lifecycle contract."""

    def read1(self, amount: int = -1) -> bytes:
        """Read one scripted transport fragment without combining it with another."""
        return self.read(amount)


class TimedResponse(io.BytesIO):
    """Model an upstream socket whose reads consume its armed idle timeout."""

    def __init__(
        self, clock: list[float], chunks: list[bytes | BaseException], content_type: str
    ) -> None:
        """Arm the idle timeout and queue the chunks that later reads return."""
        super().__init__()
        self.clock = clock
        self.chunks = iter(chunks)
        self.timeout = 0.7
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.status = 200
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self))

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    @override
    def read1(self, amount: int | None = -1) -> bytes:
        del amount
        chunk = next(self.chunks, b"")
        if isinstance(chunk, BaseException):
            raise chunk
        if not chunk:
            return b""
        delay = 0.2
        self.clock[0] += min(delay, self.timeout)
        if self.timeout < delay - 1e-9:
            raise TimeoutError("private upstream read timeout")
        return chunk

    @override
    def read(self, amount: int | None = -1) -> bytes:
        if amount is not None and amount >= 0:
            return self.read1(amount)
        chunks = []
        while chunk := self.read1():
            chunks.append(chunk)
        return b"".join(chunks)


class InputTransportFixture:
    """Reset shared relay state around each transport contract."""

    def setup_method(self) -> None:
        self._cleanups = contextlib.ExitStack()
        old_log_path = operational_log.LOG_PATH
        self._log_directory = tempfile.TemporaryDirectory()
        operational_log.LOG_PATH = str(Path(self._log_directory.name) / "proxy.log")
        self._cleanups.callback(self._log_directory.cleanup)
        self._cleanups.callback(setattr, operational_log, "LOG_PATH", old_log_path)
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        upstream_transport.reset_for_test()

    def teardown_method(self) -> None:
        self._cleanups.close()

    @staticmethod
    def _status_snapshot() -> dict[str, object]:
        return proxy.runtime_status()

    @classmethod
    def _status_maps(cls) -> tuple[dict[str, int], dict[str, int]]:
        status = cls._status_snapshot()
        counters = cast("dict[str, int]", status["counters"])
        classifications = cast("dict[str, int]", status["upstream_classifications"])
        return counters, classifications


class LoopbackTransportFixture(InputTransportFixture):
    """Run real loopback exchanges with the shared relay-state lifecycle."""

    def exchange(
        self, scripted: Sequence[ScriptedResponse], body: bytes
    ) -> tuple[list[bytes], bytes]:
        """Run one loopback exchange and return received requests and payload."""
        with (
            running_proxy(scripted) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            return received, response.read()
