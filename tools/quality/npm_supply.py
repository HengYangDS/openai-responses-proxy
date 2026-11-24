"""Require the npm on PATH to be the one that the repository's tool configuration selects."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from tools.quality.release_age import DECLARATION

ROOT = Path(__file__).resolve().parents[2]


class NpmSupplyError(RuntimeError):
    """The npm that runs is not the one that the tool configuration selects."""


def selected(root: Path = ROOT) -> str:
    """Return the npm version that `.config/mise/config.toml` selects."""
    tools = tomllib.loads((root / DECLARATION).read_text(encoding="utf-8")).get("tools", {})
    version = tools.get("npm")
    if not isinstance(version, str) or not version:
        message = f"npm_unselected:{DECLARATION} selects no npm"
        raise NpmSupplyError(message)
    return version


def running() -> str:
    """Return the version of the npm that a command named `npm` runs here."""
    executable = shutil.which("npm")
    if executable is None:
        message = "npm_unavailable: no npm on PATH"
        raise NpmSupplyError(message)
    try:
        completed = subprocess.run(
            (executable, "--version"), capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as error:
        message = "npm_unavailable: `npm --version` did not run"
        raise NpmSupplyError(message) from error
    return completed.stdout.strip()


def audit(root: Path = ROOT) -> None:
    """Refuse an npm on PATH other than the selected one, naming both."""
    wanted, found = selected(root), running()
    if found != wanted:
        message = f"npm_not_selected: npm {found} runs, {DECLARATION} selects npm {wanted}"
        raise NpmSupplyError(message)


def main() -> None:
    """Check the npm that runs, for the governance graph."""
    try:
        audit(ROOT)
    except NpmSupplyError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
