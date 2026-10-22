"""Shared fixtures for repository quality contracts."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from tools.git_environment import isolated_config_environment
from tools.quality.repository import __main__ as repository_audit

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str) -> ModuleType:
    """Load a repository module without changing import search paths."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run isolated fixture Git and fail with its diagnostic."""
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(root), *args],
        capture_output=True,
        check=False,
        env=isolated_config_environment(),
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result


@contextmanager
def repository(
    files: tuple[str, ...], *, tracked: tuple[str, ...] | None = None
) -> Generator[Path]:
    """Yield one isolated Git fixture with explicit tracked ownership."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        git(root, "init", "-q", "--initial-branch=fixture-root")
        for relative in files:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pass\n", encoding="utf-8")
        selected = files if tracked is None else tracked
        if selected:
            git(root, "add", "--", *selected)
        yield root


def quality_inventory(root: Path):
    """Compile the fixture's configured Python inventory."""
    return repository_audit._repository_inventory(root, ("src",), ("tests",))


def audit_source(source_text: str, relative: str = "source.py"):
    """Audit one isolated Python owner and return its observed structure."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(source_text, encoding="utf-8")
        policy = tomllib.loads(repository_audit.CONFIG.read_text(encoding="utf-8"))
        return repository_audit.audit_paths(
            root, [source], max_nesting_depth=policy["control_nesting"]["max_depth"]
        )
