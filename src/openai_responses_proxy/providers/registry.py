"""Load and resolve the declarative provider manifest."""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from typing import Protocol
from typing import cast

_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
POLICY_PACKAGE = "openai_responses_proxy.providers.policies"


class WirePolicy(Protocol):
    """Optional provider-specific wire outcome contract."""

    __name__: str
    EXHAUSTED_CODE: str
    EXHAUSTED_MESSAGE: str
    FAILURE_CACHE_CAPACITY: int
    FAILURE_COOLDOWN_SECONDS: int
    POLICY_VERSION: str

    def is_retryable_failure(self, status: int, payload: bytes) -> bool:
        """Return whether one provider response permits a bounded retry."""
        ...

    def request_fingerprint(self, raw: bytes) -> str:
        """Return the provider-defined identity for one request payload."""
        ...


_POLICY_MEMBERS = (
    "EXHAUSTED_CODE",
    "EXHAUSTED_MESSAGE",
    "FAILURE_CACHE_CAPACITY",
    "FAILURE_COOLDOWN_SECONDS",
    "POLICY_VERSION",
    "is_retryable_failure",
    "request_fingerprint",
)


@dataclass(frozen=True, slots=True)
class Profile:
    """One provider route and its optional wire-policy owner."""

    name: str
    base_url: str
    wire_policy: WirePolicy | None = None
    agent_message_delivery: Literal["native", "plaintext"] = "native"


@dataclass(frozen=True, slots=True)
class Registry:
    """Validated provider profiles from the release-owned manifest."""

    profiles: Mapping[str, Profile]

    def __post_init__(self) -> None:
        """Detach the registry from caller-owned mutable mappings."""
        object.__setattr__(self, "profiles", MappingProxyType(dict(self.profiles)))

    def with_upstream(self, base_url: str) -> Registry:
        """Add one explicit ordinary route without changing released profiles."""
        if not base_url:
            return self
        if "upstream" in self.profiles:
            raise ValueError("explicit upstream conflicts with a released route")
        profile = Profile("upstream", normalize_upstream(base_url))
        return Registry({**self.profiles, profile.name: profile})

    def resolve(self, path: str) -> tuple[Profile, str, str] | None:
        """Resolve one exact provider-scoped supported target."""
        try:
            parsed = urllib.parse.urlsplit(path)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return None
        parts = parsed.path.split("/")
        if (
            len(parts) != 4
            or parts[0]
            or parts[2] != "v1"
            or parts[3] not in {"models", "responses"}
        ):
            return None
        profile = self.profiles.get(parts[1])
        if profile is None or urllib.parse.quote(parsed.path, safe="/-._~") != parsed.path:
            return None
        query = f"?{parsed.query}" if parsed.query else ""
        resource = parts[3]
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
    loaded_modules: set[str] = set()
    profiles: dict[str, Profile] = {}
    try:
        for name, raw in raw_profiles.items():
            profile = _profile(name, raw)
            profiles[name] = profile
            if profile.wire_policy is not None:
                loaded_modules.add(profile.wire_policy.__name__)
    except ValueError:
        for module_name in loaded_modules:
            sys.modules.pop(module_name, None)
        raise
    if "default" in document:
        for module_name in loaded_modules:
            sys.modules.pop(module_name, None)
        raise ValueError("provider manifest has no implicit default route")
    return Registry(profiles)


def _profile(name: object, raw: object) -> Profile:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("provider names must be lowercase kebab-case slugs")
    if not isinstance(raw, dict) or set(raw) - {"base_url", "policy", "agent_message_delivery"}:
        raise ValueError(f"provider {name!r} has unknown or invalid fields")
    policy = raw.get("policy")
    if policy is not None and (not isinstance(policy, str) or not _NAME.fullmatch(policy)):
        raise ValueError(f"provider {name!r} references an unknown policy")
    delivery = raw.get("agent_message_delivery", "native")
    if delivery not in ("native", "plaintext"):
        raise ValueError(f"provider {name!r} has an invalid agent message delivery policy")
    return Profile(
        name,
        _base_url(name, raw.get("base_url")),
        None if policy is None else _load_policy(policy),
        cast(Literal["native", "plaintext"], delivery),
    )


def _load_policy(name: str) -> WirePolicy:
    """Load one closed wire-policy module from the selected policy package."""
    package = POLICY_PACKAGE
    module_name = f"{package}.{name}"
    try:
        module = importlib.import_module(module_name)
    except (
        ImportError,
        OSError,
        RuntimeError,
        SyntaxError,
        TypeError,
        ValueError,
    ) as exc:
        sys.modules.pop(module_name, None)
        raise ValueError(f"provider policy {name!r} is unavailable") from exc
    module_file = getattr(module, "__file__", None)
    package_module = sys.modules.get(package)
    package_paths = getattr(package_module, "__path__", ())
    try:
        if not isinstance(module_file, str):
            raise TypeError
        resolved = Path(module_file).resolve(strict=True)
        inside_package = any(
            resolved.is_relative_to(Path(root).resolve(strict=True)) for root in package_paths
        )
    except (OSError, TypeError, ValueError):
        specification = getattr(module, "__spec__", None)
        inside_package = all(
            (
                bool(getattr(sys, "frozen", False)),
                module.__name__ == module_name,
                module.__package__ == package,
                getattr(specification, "name", None) == module_name,
            )
        )
    if not inside_package:
        sys.modules.pop(module_name, None)
        raise ValueError(f"provider policy {name!r} is outside its policy package")
    (
        exhausted_code,
        exhausted_message,
        cache_capacity,
        cooldown_seconds,
        policy_version,
        classify_failure,
        fingerprint_request,
    ) = (getattr(module, member, None) for member in _POLICY_MEMBERS)
    if (
        any(
            not isinstance(value, str) or not value
            for value in (exhausted_code, exhausted_message, policy_version)
        )
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (cache_capacity, cooldown_seconds)
        )
        or not callable(classify_failure)
        or not callable(fingerprint_request)
    ):
        sys.modules.pop(module_name, None)
        raise ValueError(f"provider policy {name!r} does not implement the wire-policy contract")
    return cast(WirePolicy, module)


def policy_module_names(registry: Registry) -> tuple[str, ...]:
    """Return the distinct loaded wire-policy modules in deterministic order."""
    return tuple(
        sorted(
            {
                policy.__name__
                for profile in registry.profiles.values()
                if (policy := profile.wire_policy) is not None
            }
        )
    )


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


def normalize_upstream(value: str) -> str:
    """Validate a secret-free ordinary Responses upstream using manifest rules."""
    return _base_url("upstream", value)
