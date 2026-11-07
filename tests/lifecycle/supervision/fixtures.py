"""Shared supervision test construction helpers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import install_context

if TYPE_CHECKING:
    from collections.abc import Generator
    from collections.abc import Iterable
    from collections.abc import Sequence

    from openai_responses_proxy.lifecycle import context as runtime_context


def capture_generation(pid: int, expected_path: str) -> process.OwnedProcess | None:
    """Capture an already-proven PID as one exact process generation without reading argv."""
    try:
        created_at = process._created_at(psutil.Process(pid))
    except (OSError, TypeError, psutil.Error):
        return None
    return process.OwnedProcess(
        pid,
        os.path.normcase(os.path.realpath(os.path.abspath(expected_path))),
        created_at,
    )


def completed(
    cmd: Sequence[str] = (),
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
) -> subprocess.CompletedProcess[str | bytes]:
    """Build a deterministic completed-process result."""
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


@contextmanager
def temporary_context(
    attribute: str, *, windows: bool = False
) -> Generator[runtime_context.RuntimeContext]:
    """Yield a service context whose host-owned paths share one temporary root."""
    with tempfile.TemporaryDirectory() as directory:
        context = install_context(Path(directory), windows=windows)
        setattr(context, attribute, directory)
        if attribute == "install_dir":
            context.executable = inventory.installed_executable(directory, windows=windows)
        yield context


def set_file(path: str | Path, text: str | None = None) -> Path:
    """Create or remove an LF text fixture, as native adapters write it, and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def assert_fragments(text: str, include: Iterable[str] = (), exclude: Iterable[str] = ()) -> None:
    """Assert required and forbidden fragments."""
    for fragment in include:
        assert fragment in text
    for fragment in exclude:
        assert fragment not in text
