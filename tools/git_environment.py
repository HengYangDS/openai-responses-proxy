"""Canonical environments for repository-owned Git subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping

_CONFIG_ISOLATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

_IMMUTABLE_REMOTE_PROOF = {
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


def isolated_config_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the host environment without personal Git configuration."""
    environment = os.environ | _CONFIG_ISOLATION
    if overrides is not None:
        environment.update(overrides)
    return environment


def immutable_remote_proof_environment() -> dict[str, str]:
    """Return a non-interactive Git environment without inherited Git state."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(_CONFIG_ISOLATION)
    environment.update(_IMMUTABLE_REMOTE_PROOF)
    return environment
