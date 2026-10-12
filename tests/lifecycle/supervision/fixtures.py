"""Shared supervision test construction helpers."""

from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from openai_responses_proxy.service import inventory
from tests.lifecycle.fixtures import install_context


def completed(cmd=(), returncode=0, stdout="", stderr=""):
    """Build a deterministic completed-process result."""
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


@contextmanager
def temporary_context(attribute, *, windows=False):
    """Yield a service context whose host-owned paths share one temporary root."""
    with tempfile.TemporaryDirectory() as directory:
        context = install_context(Path(directory), windows=windows)
        setattr(context, attribute, directory)
        if attribute == "install_dir":
            context.executable = inventory.installed_executable(directory, windows=windows)
        yield context


def set_file(path, text=None):
    """Create or remove an LF text fixture, as native adapters write it, and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def assert_fragments(text, include=(), exclude=()):
    """Assert required and forbidden fragments."""
    for fragment in include:
        assert fragment in text
    for fragment in exclude:
        assert fragment not in text
