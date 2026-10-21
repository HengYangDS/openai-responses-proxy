"""Run the repository's provider-neutral governance checks once."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

from tools.ci.project import model_inputs

ROOT = Path(__file__).resolve().parents[2]
LINK_POLICY = ".config/checks/lychee/lychee.toml"
PYTEST_CONFIG = ".config/checks/pytest/pytest.toml"
DEPENDENCY_POLICY = ROOT / ".config/checks/deptry/policy.toml"


def _tracked_current(suffixes: tuple[str, ...]) -> tuple[str, ...]:
    """Admit complete index-owned inputs before any native formatter runs."""
    completed = subprocess.run(
        (
            "git",
            "--git-dir=.git",
            "--work-tree=.",
            "ls-files",
            "-t",
            "--stage",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *(f"*{suffix}" for suffix in suffixes),
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths: set[str] = set()
    for entry in completed.stdout.decode().split("\0"):
        if not entry:
            continue
        tag, _, record = entry.partition(" ")
        metadata, separator, relative = record.partition("\t")
        relative = record if tag == "?" or not separator else relative
        if not relative.endswith(suffixes) or relative.startswith("openspec/changes/archive/"):
            continue
        if tag == "?":
            raise GovernanceError(f"formatter_input_untracked:{relative}")
        fields = metadata.split()
        if (
            tag not in {"H", "S"}
            or not separator
            or len(fields) != 3
            or fields[0] not in {"100644", "100755"}
            or fields[2] != "0"
        ):
            raise GovernanceError(f"formatter_input_index_invalid:{relative}")
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or path.resolve() != ROOT.resolve() / relative:
            raise GovernanceError(f"formatter_input_unavailable:{relative}")
        paths.add(relative)
    if not paths:
        raise GovernanceError("formatter_input_empty")
    return tuple(sorted(paths))


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


def _formatter_commands(
    markdown_yaml: tuple[str, ...], toml: tuple[str, ...], *, write: bool
) -> tuple[tuple[str, ...], ...]:
    """Bind Prettier and taplo to one tracked input set for both check and fix."""
    return (
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "prettier",
            "--write" if write else "--check",
            "--config",
            ".config/checks/prettier/prettier.toml",
            "--no-editorconfig",
            "--ignore-path",
            os.devnull,
            *markdown_yaml,
        ),
        (
            "taplo",
            "format",
            *(() if write else ("--check",)),
            "--config",
            ".config/checks/taplo/taplo.toml",
            *toml,
        ),
    )


def _commands(*, online_links: bool) -> tuple[tuple[str, ...], ...]:
    """Return the single ordered governance graph for this repository."""
    link_mode = () if online_links else ("--offline",)
    markdown_yaml = _tracked_current((".md", ".yaml", ".yml"))
    markdown = tuple(path for path in markdown_yaml if path.endswith(".md"))
    toml = _tracked_current((".toml",))
    return (
        *_formatter_commands(markdown_yaml, toml, write=False),
        ("cue", "fmt", "--check", "--files", *model_inputs()),
        ("cue", "vet", *model_inputs()),
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


def _run(commands: Iterable[tuple[str, ...]]) -> None:
    """Execute commands in order and stop at the first unavailable or failing tool."""
    for command in commands:
        try:
            completed = subprocess.run(command, cwd=ROOT, check=False)
        except OSError as error:
            raise GovernanceError(f"governance tool unavailable: {command[0]}") from error
        if completed.returncode:
            raise GovernanceError(f"governance check failed: {command[0]}")


def audit(*, online_links: bool = False) -> None:
    """Execute every governance concern from one composition root."""
    _run(_commands(online_links=online_links))


def fix() -> None:
    """Apply native formatters to the exact inputs that audit checks."""
    markdown_yaml = _tracked_current((".md", ".yaml", ".yml"))
    toml = _tracked_current((".toml",))
    _run(
        (
            *_formatter_commands(markdown_yaml, toml, write=True),
            ("cue", "fmt", "--files", *model_inputs()),
        )
    )


def _command(
    *,
    online_links: bool = False,
    fix_formatting: Annotated[bool, Parameter(name="--fix", negative=False)] = False,
) -> None:
    """Run deterministic checks, optionally including external links, or apply formatters."""
    if fix_formatting:
        fix()
    else:
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
