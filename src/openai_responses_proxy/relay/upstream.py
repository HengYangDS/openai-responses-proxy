"""Upstream response capabilities consumed by the downstream relay."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


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
