"""Canonical annotated release-tag identity over the product release version."""

from __future__ import annotations

from openai_responses_proxy.lifecycle.records import is_version


def is_tag(value: str) -> bool:
    """Return whether ``value`` is one strict annotated release-tag name."""
    return value.startswith("v") and is_version(value[1:])


def version_from_tag(value: str) -> str:
    """Return strict SemVer from one valid release tag."""
    if not is_tag(value):
        raise ValueError("release tag must be exact vMAJOR.MINOR.PATCH")
    return value[1:]
