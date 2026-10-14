"""Run the repository's provider-neutral governance checks once."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

from cyclopts import App

ROOT = Path(__file__).resolve().parents[2]
LINK_POLICY = ".config/checks/lychee/lychee.toml"
PYTEST_CONFIG = ".config/checks/pytest/pytest.toml"
DEPENDENCY_POLICY = ROOT / ".config/checks/deptry/policy.toml"


def _tracked_current(suffixes: tuple[str, ...]) -> tuple[str, ...]:
    """Return tracked current authorities for one native formatter."""
    completed = subprocess.run(
        ("git", "--git-dir=.git", "--work-tree=.", "ls-files", "-z"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        path
        for path in completed.stdout.decode().split("\0")
        if path and path.endswith(suffixes) and not path.startswith("openspec/changes/archive/")
    )


class GovernanceError(RuntimeError):
    """A repository governance concern failed."""


def _dependency_options() -> tuple[str, ...]:
    """Pass the repository dependency policy to deptry as native options."""
    policy = tomllib.loads(DEPENDENCY_POLICY.read_text(encoding="utf-8"))
    first_party = policy.get("known_first_party")
    module_names = policy.get("package_module_name_map")
    if not isinstance(first_party, list) or not isinstance(module_names, dict):
        raise GovernanceError("dependency policy is malformed")
    modules = ",".join(
        f"{package}={'|'.join(str(name) for name in names)}"
        for package, names in sorted(module_names.items())
    )
    return (
        "--known-first-party",
        ",".join(str(name) for name in first_party),
        "--package-module-name-map",
        modules,
    )


def _commands(*, online_links: bool) -> tuple[tuple[str, ...], ...]:
    """Return the single ordered governance graph for this repository."""
    link_mode = () if online_links else ("--offline",)
    markdown_yaml = _tracked_current((".md", ".yaml", ".yml"))
    markdown = tuple(path for path in markdown_yaml if path.endswith(".md"))
    toml = _tracked_current((".toml",))
    return (
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "prettier",
            "--check",
            "--config",
            ".config/checks/prettier/prettier.toml",
            *markdown_yaml,
        ),
        (
            "taplo",
            "format",
            "--check",
            "--config",
            ".config/checks/taplo/taplo.toml",
            *toml,
        ),
        ("cue", "fmt", "--check", "--files", ".config/ci/pipeline.cue"),
        ("cue", "vet", ".config/ci/pipeline.cue"),
        (sys.executable, "-m", "tools.ci.project"),
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "openspec",
            "validate",
            "--all",
            "--strict",
            "--no-interactive",
        ),
        ("mise", "run", "--timeout", "60s", "workflow-check"),
        (
            "deptry",
            "src/openai_responses_proxy",
            "--config",
            "pyproject.toml",
            *_dependency_options(),
            "--no-ansi",
        ),
        (
            "vulture",
            "src/openai_responses_proxy",
            "tools",
            "--config",
            ".config/checks/vulture/vulture.toml",
        ),
        (
            "gitleaks",
            "git",
            "--config",
            ".config/checks/gitleaks/gitleaks.toml",
            "--platform",
            "gitlab",
            "--redact",
            "--no-banner",
            ".",
        ),
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--rootdir",
            ".",
            "-c",
            PYTEST_CONFIG,
            "tests/quality/secrets",
        ),
        ("lychee", "--config", LINK_POLICY, *link_mode, *markdown),
        (sys.executable, "-m", "tools.release.metadata"),
        (sys.executable, "-m", "tools.quality.hard_coding"),
        (sys.executable, "-m", "tools.quality.repository"),
    )


def audit(*, online_links: bool = False) -> None:
    """Execute every governance concern from one composition root."""
    for command in _commands(online_links=online_links):
        try:
            completed = subprocess.run(command, cwd=ROOT, check=False)
        except OSError as error:
            raise GovernanceError(f"governance tool unavailable: {command[0]}") from error
        if completed.returncode:
            raise GovernanceError(f"governance check failed: {command[0]}")


def _command(*, online_links: bool = False) -> None:
    """Run deterministic checks, optionally including external links."""
    audit(online_links=online_links)


def main(argv: Iterable[str] = ()) -> None:
    """Run repository governance through the shared parser stack."""
    try:
        App(default_command=_command, help=__doc__, result_action="return_value")(tuple(argv))
    except GovernanceError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main(sys.argv[1:])
