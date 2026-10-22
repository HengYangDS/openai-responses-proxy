"""Shared hosted-Forge CLI transport primitives."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


def executable(name: str, error_type: type[Exception]) -> str:
    """Resolve one required Forge CLI to a canonical absolute path."""
    candidate = shutil.which(name)
    if candidate is None:
        raise error_type(f"{name} is unavailable")
    return str(Path(candidate).resolve())


def api_json(
    command: Sequence[str],
    *,
    unavailable: str,
    error_type: type[Exception],
) -> object:
    """Run one read-only Forge CLI request and decode its JSON response."""
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise error_type(unavailable) from error


def api_bytes(command: Sequence[str], *, unavailable: str, error_type: type[Exception]) -> bytes:
    """Run one read-only request and return its exact response bytes."""
    try:
        return subprocess.run(command, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise error_type(unavailable) from error
