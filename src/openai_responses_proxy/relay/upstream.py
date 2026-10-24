"""Upstream response capabilities consumed by the downstream relay."""

from __future__ import annotations

import http.client
import io
import ipaddress
import queue
import socket
import ssl
import threading
import time
import urllib.request
from collections.abc import Buffer
from collections.abc import Iterable
from functools import partial
from typing import IO
from typing import Literal
from typing import Protocol
from typing import cast
from typing import override
from typing import runtime_checkable


class ExchangeDeadlineError(TimeoutError):
    """The original upstream exchange budget has expired."""


class ResponseSizeError(ValueError):
    """A buffered upstream body exceeds its admitted byte limit."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExchangeDeadlineError("upstream exchange deadline expired during connection setup")
    return remaining


_AddressInfo = (
    tuple[Literal[socket.AddressFamily.AF_INET], socket.SocketKind, int, str, tuple[str, int]]
    | tuple[
        Literal[socket.AddressFamily.AF_INET6],
        socket.SocketKind,
        int,
        str,
        tuple[str, int, int, int] | tuple[int, bytes],
    ]
)


class _Lookup:
    """One native address lookup that several exchanges can wait for."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.addresses: list[_AddressInfo] = []
        self.error: BaseException | None = None


class _Resolver:
    """Run native ``getaddrinfo`` on a small fixed set of daemon threads.

    ``getaddrinfo`` cannot be interrupted, so an exchange waits for its lookup
    only until its own deadline. Exchanges that need the same address share one
    lookup and at most four lookups run at once; a lookup that outlives every
    waiter ends on its thread without holding an exchange, an upstream
    connection or an admission slot.
    """

    def __init__(self, threads: int) -> None:
        self._threads = threads
        self._started = 0
        self._lock = threading.Lock()
        self._queue: queue.SimpleQueue[tuple[tuple[str, int], _Lookup]] = queue.SimpleQueue()
        self._pending: dict[tuple[str, int], _Lookup] = {}

    def resolve(self, host: str, port: int, deadline: float) -> list[_AddressInfo]:
        key = (host, port)
        with self._lock:
            lookup = self._pending.get(key)
            if lookup is None:
                lookup = _Lookup()
                self._pending[key] = lookup
                self._queue.put((key, lookup))
                if self._started < self._threads:
                    self._started += 1
                    threading.Thread(
                        target=self._work, name="upstream-resolver", daemon=True
                    ).start()
        if not lookup.done.wait(_remaining(deadline)):
            raise ExchangeDeadlineError(
                "upstream address resolution exceeded the exchange deadline"
            )
        if lookup.error is not None:
            raise lookup.error.with_traceback(None)
        return lookup.addresses

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()

    def _work(self) -> None:
        while True:
            key, lookup = self._queue.get()
            try:
                lookup.addresses = socket.getaddrinfo(*key, 0, socket.SOCK_STREAM)
            except BaseException as error:
                lookup.error = error
            finally:
                with self._lock:
                    if self._pending.get(key) is lookup:
                        del self._pending[key]
                lookup.done.set()


_RESOLVER = _Resolver(threads=4)


def resolve(host: str, port: int, deadline: float) -> list[_AddressInfo]:
    """Resolve one upstream address within ``deadline`` through the native resolver."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _RESOLVER.resolve(host, port, deadline)
    # A numeric address needs no lookup, so it never waits for a resolver thread.
    return socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)


def reset_for_test() -> None:
    """Forget pending lookups between isolated tests."""
    _RESOLVER.reset()


def _connect(
    address: tuple[str, int],
    timeout: float,
    source_address: tuple[str, int] | None,
    *,
    deadline: float,
) -> socket.socket:
    """Resolve natively within the deadline, then spend one budget across address attempts and TLS."""
    _remaining(deadline)
    addresses = resolve(*address, deadline)
    _remaining(deadline)
    failure: OSError | None = None
    for family, kind, protocol, _, target in addresses:
        budget = min(timeout, _remaining(deadline))
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(budget)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            sock.settimeout(min(timeout, _remaining(deadline)))
        except OSError as error:
            sock.close()
            failure = error
            _remaining(deadline)
        except BaseException:
            sock.close()
            raise
        else:
            return sock
    if failure is not None:
        raise failure
    raise OSError("upstream address resolution returned no stream address")


class _DeadlineReader(io.RawIOBase):
    """Clamp every native socket read, including HTTP header and chunk framing."""

    def __init__(self, raw: socket.SocketIO, sock: socket.socket, deadline: float) -> None:
        super().__init__()
        self._raw = raw
        self._sock = sock
        self._deadline = deadline
        self._idle_timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        """Select an idle limit independently of the socket's last armed budget."""
        self._idle_timeout = timeout

    @override
    def readable(self) -> bool:
        return self._raw.readable()

    @override
    def readinto(self, buffer: Buffer) -> int | None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ExchangeDeadlineError("upstream deadline expired before socket read")
        idle_timeout = self._idle_timeout
        deadline_bound = idle_timeout is None or remaining <= idle_timeout
        self._sock.settimeout(remaining if deadline_bound else idle_timeout)
        try:
            return self._raw.readinto(buffer)
        except TimeoutError as error:
            if deadline_bound or time.monotonic() >= self._deadline:
                raise ExchangeDeadlineError(
                    "upstream socket exhausted its deadline budget"
                ) from error
            raise

    @override
    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


def _bind_response(connection: http.client.HTTPConnection, deadline: float) -> None:
    """Select the native response-class extension without changing HTTP parsing."""

    class Response(http.client.HTTPResponse):
        @override
        def __init__(
            self,
            sock: socket.socket,
            debuglevel: int = 0,
            method: str | None = None,
            url: str | None = None,
        ) -> None:
            super().__init__(sock, debuglevel, method, url)
            buffered = cast(io.BufferedReader, self.fp)
            raw = cast(socket.SocketIO, buffered.detach())
            self.fp = io.BufferedReader(_DeadlineReader(raw, sock, deadline))

    connection.response_class = Response


class _ReadableBody(Protocol):
    def read(self, size: int = -1, /) -> bytes:
        """Read native request-body bytes."""
        ...


class _DeadlineConnection(http.client.HTTPConnection):
    """Spend the exchange budget at native connection, write and read boundaries."""

    _deadline: float

    @override
    def connect(self) -> None:
        self._create_connection = partial(_connect, deadline=self._deadline)
        super().connect()
        if self.sock is not None:
            self.sock.settimeout(_remaining(self._deadline))

    @override
    def send(self, data: _ReadableBody | Iterable[Buffer] | Buffer | str) -> None:
        _remaining(self._deadline)
        if self.sock is not None:
            self.sock.settimeout(_remaining(self._deadline))
        super().send(data)

    @override
    def getresponse(self) -> http.client.HTTPResponse:
        _bind_response(self, self._deadline)
        return super().getresponse()


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    """Use the official HTTP connection with deadline-aware raw reads."""

    def __init__(self, deadline: float) -> None:
        super().__init__()
        self._deadline = deadline

    @override
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        deadline = self._deadline

        class Connection(_DeadlineConnection):
            _deadline = deadline

        return self.do_open(Connection, req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    """Preserve native certificate verification and bound decrypted HTTP reads."""

    def __init__(self, deadline: float, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._deadline = deadline
        self._tls_context = context

    @override
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        deadline = self._deadline

        class Connection(_DeadlineConnection, http.client.HTTPSConnection):
            _deadline = deadline

        return self.do_open(Connection, req, context=self._tls_context)


class RefuseRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return every upstream redirect to the client instead of following it.

    The standard handler re-sends the client's headers, including its credential, to any host
    and scheme that a redirect names; the relay sends requests only to the selected upstream.
    """

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        return None


def deadline_opener(deadline: float, context: ssl.SSLContext) -> urllib.request.OpenerDirector:
    """Create one proxy-free native opener scoped to this request's deadline."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        RefuseRedirectHandler(),
        _DeadlineHTTPHandler(deadline),
        _DeadlineHTTPSHandler(deadline, context),
    )


@runtime_checkable
class _TimeoutSocket(Protocol):
    """Transport capability for bounding the next blocking socket operation."""

    def settimeout(self, timeout: float) -> None:
        """Set the next blocking operation's timeout."""
        ...


class ResponseHeaders(Protocol):
    """Header operations consumed by the downstream projection."""

    def items(self) -> Iterable[tuple[str, str]]:
        """Iterate over response header pairs."""
        ...


def header_value(headers: ResponseHeaders, name: str, default: str = "") -> str:
    """Read one case-insensitive header without coupling to its container type."""
    normalized = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == normalized),
        default,
    )


class UpstreamResponse(Protocol):
    """Portable response surface returned by the standard-library opener."""

    @property
    def status(self) -> int:
        """Return the upstream HTTP status."""
        ...

    @property
    def headers(self) -> ResponseHeaders:
        """Return the upstream response headers."""
        ...

    @property
    def fp(self) -> object:
        """Return the implementation-defined transport handle."""
        ...

    def read(self, amount: int = -1) -> bytes:
        """Read at most ``amount`` bytes."""
        ...

    def read1(self, amount: int = -1) -> bytes:
        """Return available bytes without filling the buffer across HTTP chunks."""
        ...

    def close(self) -> None:
        """Release the upstream transport."""
        ...


def set_read_timeout(response: UpstreamResponse, timeout: float) -> None:
    """Arm the standard-library response or HTTP-error socket in place."""
    for path in (
        ("raw",),
        ("fp", "raw"),
        ("raw", "_sock"),
        ("raw", "_fp", "fp", "raw", "_sock"),
        ("fp", "raw", "_sock"),
        ("fp", "raw", "_fp", "fp", "raw", "_sock"),
    ):
        candidate = response.fp
        for attribute in path:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if isinstance(candidate, _TimeoutSocket):
            candidate.settimeout(timeout)
            return


def read_chunk(
    response: UpstreamResponse,
    deadline: float,
    max_bytes: int,
    *,
    idle_timeout: float | None = None,
) -> bytes:
    """Read one chunk within the original deadline and an optional narrower idle limit."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExchangeDeadlineError("upstream exchange deadline expired during body read")
    set_read_timeout(
        response, min(remaining, idle_timeout) if idle_timeout is not None else remaining
    )
    try:
        chunk = response.read1(max_bytes)
    except Exception as error:
        if time.monotonic() >= deadline:
            raise ExchangeDeadlineError("upstream exchange body deadline expired") from error
        raise
    if time.monotonic() >= deadline:
        raise ExchangeDeadlineError("upstream exchange body deadline expired")
    return chunk


def read_body(response: UpstreamResponse, deadline: float, max_bytes: int) -> bytes:
    """Read a complete bounded body without refunding header or retry time."""
    payload = bytearray()
    while True:
        chunk = read_chunk(response, deadline, min(8192, max_bytes + 1 - len(payload)))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise ResponseSizeError("upstream body exceeds its admitted byte limit")
