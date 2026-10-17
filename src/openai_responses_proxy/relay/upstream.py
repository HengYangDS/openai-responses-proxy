"""Upstream response capabilities consumed by the downstream relay."""

from __future__ import annotations

import http.client
import io
import socket
import ssl
import time
import urllib.request
from collections.abc import Buffer
from collections.abc import Iterable
from functools import partial
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


def _connect(
    address: tuple[str, int],
    timeout: float,
    source_address: tuple[str, int] | None,
    *,
    deadline: float,
) -> socket.socket:
    """Resolve natively, then spend one budget across address attempts and TLS."""
    _remaining(deadline)
    addresses = socket.getaddrinfo(*address, 0, socket.SOCK_STREAM)
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

    @override
    def readable(self) -> bool:
        return self._raw.readable()

    @override
    def readinto(self, buffer: Buffer) -> int | None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ExchangeDeadlineError("upstream deadline expired before socket read")
        idle_timeout = self._sock.gettimeout()
        self._sock.settimeout(min(remaining, idle_timeout) if idle_timeout else remaining)
        return self._raw.readinto(buffer)

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


def deadline_opener(deadline: float, context: ssl.SSLContext) -> urllib.request.OpenerDirector:
    """Create one proxy-free native opener scoped to this request's deadline."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
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
    set_read_timeout(response, min(remaining, idle_timeout) if idle_timeout else remaining)
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
