"""Load and resolve the declarative provider manifest."""

from __future__ import annotations

import re
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class Profile:
    """One provider route."""

    name: str
    base_url: str


@dataclass(frozen=True, slots=True)
class Registry:
    """Validated provider profiles from the release-owned manifest."""

    profiles: Mapping[str, Profile]

    def __post_init__(self) -> None:
        """Detach the registry from caller-owned mutable mappings."""
        object.__setattr__(self, "profiles", MappingProxyType(dict(self.profiles)))

    def resolve(self, path: str) -> tuple[Profile, str, str] | None:
        """Resolve one exact provider-scoped supported target."""
        try:
            parsed = urllib.parse.urlsplit(path)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return None
        match parsed.path.split("/"):
            case ["", provider, "v1", ("models" | "responses") as resource]:
                profile = self.profiles.get(provider)
            case _:
                return None
        if profile is None or urllib.parse.quote(parsed.path, safe="/-._~") != parsed.path:
            return None
        query = f"?{parsed.query}" if parsed.query else ""
        return profile, resource, profile.base_url + "/" + resource + query


def default_manifest_path() -> Path:
    """Return the provider manifest owned by this semantic package."""
    return Path(__file__).with_name("manifest.toml")


def load(path: str | Path | None = None) -> Registry:
    """Load the released manifest or an explicit test/admission fixture."""
    selected = Path(path) if path is not None else default_manifest_path()
    try:
        with selected.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot load provider manifest: {selected}") from exc
    if document.get("version") != 1:
        raise ValueError("provider manifest version must be 1")
    raw_profiles = document.get("providers")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError("provider manifest must define at least one provider")
    if "default" in document:
        raise ValueError("provider manifest has no implicit default route")
    profiles = {name: _profile(name, raw) for name, raw in raw_profiles.items()}
    return Registry(profiles)


def _profile(name: object, raw: object) -> Profile:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("provider names must be lowercase kebab-case slugs")
    if not isinstance(raw, dict) or set(raw) - {"base_url"}:
        raise ValueError(f"provider {name!r} has unknown or invalid fields")
    return Profile(name, _base_url(name, raw.get("base_url")))


def _base_url(name: str, value: object) -> str:
    message = f"provider {name!r} base_url must be an absolute HTTP(S) URL"
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ValueError(message)
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError(message) from None
    if (
        parsed.scheme.lower() not in ("http", "https")
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(message)
    if parsed.scheme.lower() == "http" and parsed.hostname not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise ValueError(message)
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")
    )
