"""Resolve the upstream that a request path names.

A client's base URL is `http://<listener>/<scheme>/<authority>/<base path>`, so every data
request names its upstream origin and resource. Nothing here names a provider.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Literal
from typing import cast

from openai_responses_proxy.relay.admission import LOOPBACK_HOSTS

_SCHEMES = frozenset({"http", "https"})
_RESOURCES = frozenset({"responses", "models"})
_AUTHORITY_SAFE = "-._~:[]"
_SEGMENT_SAFE = "-._~"


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
    host = _host(authority)
    if host is None or (scheme == "http" and host not in LOOPBACK_HOSTS):
        return None
    origin = "/".join([f"{scheme}://{authority}", *base])
    query = f"?{parsed.query}" if parsed.query else ""
    selected = cast("Literal['responses', 'models']", resource)
    return Target(origin, selected, f"{origin}/{resource}{query}")


def _is_quoted(segment: str, safe: str) -> bool:
    return urllib.parse.quote(segment, safe=safe) == segment


def _host(authority: str) -> str | None:
    """Return the host of an authority with an optional valid port and no user information."""
    try:
        split = urllib.parse.urlsplit(f"//{authority}")
        _ = split.port
    except ValueError:
        return None
    if split.username is not None or authority.endswith(":"):
        return None
    return split.hostname or None
