"""Resolve the upstream that a request path names.

A client's base URL is `http://<listener>/<scheme>/<authority>/<base path>`, so every data
request names its upstream origin and resource. Nothing here names a provider.

Each upstream has one spelling: the authority is written in the normal form of RFC 3986 sections
6.2.2.1 and 6.2.3, so spellings that name one upstream share its cooldowns, pooled connections and
log lines, and the upstream receives the same URL.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from typing import Literal
from typing import cast

from openai_responses_proxy.relay.admission import LOOPBACK_HOSTS

_DEFAULT_PORTS = {"https": 443, "http": 80}
_SCHEMES = frozenset(_DEFAULT_PORTS)
_RESOURCES = frozenset({"responses", "models"})
_AUTHORITY_SAFE = "-._~:[]"
_SEGMENT_SAFE = "-._~"
# A DNS name: labels of letters, digits, `-` and `_`, 1 to 63 octets each, 253 octets in all.
_LABEL = re.compile(r"[a-z0-9_-]{1,63}")
_NAME_OCTETS = 253
_PORT_MAX = 65535
# A last label that a resolver reads as a number makes the whole host an IPv4 address.
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")


@dataclass(frozen=True, slots=True)
class Target:
    """One upstream resource named by a request path."""

    origin: str
    """Scheme, authority and base path, without a trailing slash or query.

    It keys cooldowns and names the upstream in log lines.
    """
    resource: Literal["responses", "models"]
    url: str
    """The origin, the resource and the request's query: where the request is sent."""


def resolve(path: str) -> Target | None:
    """Return the upstream that `path` names, or None when it is not an exact supported target."""
    try:
        parsed = urllib.parse.urlsplit(path)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    match parsed.path.split("/"):
        case ["", scheme, authority, *base, resource] if (
            scheme in _SCHEMES and resource in _RESOURCES
        ):
            pass
        case _:
            return None
    if not _is_quoted(authority, _AUTHORITY_SAFE) or not all(
        segment not in ("", ".", "..") and _is_quoted(segment, _SEGMENT_SAFE) for segment in base
    ):
        return None
    canonical = _canonical_authority(scheme, authority)
    if canonical is None:
        return None
    host, written = canonical
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        return None
    origin = "/".join([f"{scheme}://{written}", *base])
    query = f"?{parsed.query}" if parsed.query else ""
    selected = cast("Literal['responses', 'models']", resource)
    return Target(origin, selected, f"{origin}/{resource}{query}")


def _is_quoted(segment: str, safe: str) -> bool:
    return urllib.parse.quote(segment, safe=safe) == segment


def _canonical_authority(scheme: str, authority: str) -> tuple[str, str] | None:
    """Return the host and the normal authority, or None when it names no upstream exactly."""
    if authority.startswith("["):
        literal, bracket, rest = authority[1:].partition("]")
        host = _ipv6(literal) if bracket else None
        written = None if host is None else f"[{host}]"
    else:
        literal, colon, port = authority.partition(":")
        rest = colon + port
        host = written = _name_or_ipv4(literal.lower())
    if host is None or written is None:
        return None
    # RFC 3986, section 6.2.3: an empty port is the scheme's default.
    if rest in {"", ":"}:
        return host, written
    digits = rest.removeprefix(":")
    if len(digits) == len(rest) or not digits.isascii() or not digits.isdecimal():
        return None
    port = int(digits)
    if not 0 < port <= _PORT_MAX:
        return None
    return host, written if port == _DEFAULT_PORTS[scheme] else f"{written}:{port}"


def _ipv6(literal: str) -> str | None:
    """Return an IPv6 literal in the form of RFC 5952; refuse zone identifiers and IPvFuture."""
    if "%" in literal:
        return None
    try:
        return ipaddress.IPv6Address(literal).compressed
    except ValueError:
        return None


def _name_or_ipv4(host: str) -> str | None:
    """Return a lower-case DNS name or four decimal octets; refuse what resolvers read otherwise."""
    labels = host.split(".")
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError:
            return None
        return host if str(address) == host else None
    if len(host) > _NAME_OCTETS or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    return host
