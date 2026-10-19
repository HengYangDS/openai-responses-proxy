"""Audit the repository-owned Python quality scope and explicit contracts."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tokenize
import tomllib
from bisect import bisect_right
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from typing import TypedDict

from cyclopts import App

from tools.quality.python_matrix import supported_requirement
from tools.quality.repository.names import semantic_name_gaps
from tools.quality.repository.topology import architecture_gaps

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / ".config/checks/architecture/policy.toml"
PROJECT = ROOT / "pyproject.toml"
_DEFINITION_TYPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


class SourceMetrics(TypedDict):
    """Descriptive structure measurements for one repository-owned Python file."""

    path: str
    logical_statements: int
    effective_lines: int
    max_function_lines: int
    max_nesting_depth: int


class AuditReport(TypedDict):
    """Repository audit result shared by its command and direct consumers."""

    ok: bool
    gaps: list[str]
    policy_errors: list[str]
    files: list[SourceMetrics]
    configured_paths: dict[str, list[str]]


@dataclass(frozen=True)
class RepositoryInventory:
    """Index-owned quality paths plus fail-closed checkout gaps."""

    paths: tuple[Path, ...]
    gaps: tuple[str, ...]


def _in_scope(relative: str, configured_roots: Iterable[str]) -> bool:
    """Return whether a repository-relative path belongs to a configured root."""
    path = PurePosixPath(relative)
    for configured in configured_roots:
        configured_path = PurePosixPath(configured)
        if any(char in configured for char in "*?["):
            if len(path.parts) == len(configured_path.parts) and path.match(configured):
                return True
        elif path == configured_path or (
            configured_path.suffix != ".py" and configured_path in path.parents
        ):
            return True
    return False


def _physical_python_paths(root: Path, configured_roots: Iterable[str]) -> set[str]:
    """Resolve configured files and directories into one checkout inventory."""
    paths: set[str] = set()
    for configured in configured_roots:
        matches = (
            sorted(root.glob(configured))
            if any(char in configured for char in "*?[")
            else [root / configured]
        )
        for path in matches:
            if path.suffix == ".py" and (path.is_file() or path.is_symlink()):
                paths.add(path.relative_to(root).as_posix())
            elif path.is_dir():
                paths.update(
                    candidate.relative_to(root).as_posix()
                    for candidate in path.rglob("*.py")
                    if "__pycache__" not in candidate.parts
                    and (candidate.is_file() or candidate.is_symlink())
                )
    return paths


def _index_entries(root: Path) -> tuple[dict[str, str], list[str]]:
    """Read stage-zero index modes without treating the working tree as ownership truth."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "--git-dir=.git",
                "--work-tree=.",
                "ls-files",
                "--stage",
                "-z",
            ],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {}, [f"quality_inventory_git_unavailable:{exc}"]
    if result.returncode:
        detail = os.fsdecode(result.stderr).strip() or str(result.returncode)
        return {}, [f"quality_inventory_git_failed:{detail}"]

    entries: dict[str, str] = {}
    unmerged: set[str] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, encoded_path = raw.split(b"\t", 1)
            mode, _, stage = metadata.split()
        except ValueError:
            return {}, ["quality_inventory_git_output_malformed"]
        path = os.fsdecode(encoded_path)
        if stage != b"0":
            unmerged.add(path)
        else:
            entries[path] = os.fsdecode(mode)
    return entries, (
        [f"quality_inventory_unmerged:{','.join(sorted(unmerged))}"] if unmerged else []
    )


def _has_symlink(root: Path, relative: str) -> bool:
    """Reject a file reached through a symlink at any repository-relative component."""
    path = PurePosixPath(relative)
    return any((root.joinpath(*parent.parts)).is_symlink() for parent in (path, *path.parents[:-1]))


def _repository_inventory(
    root: Path, source_roots: Iterable[str], test_roots: Iterable[str]
) -> RepositoryInventory:
    """Return index-owned regular Python files and fail-closed checkout gaps."""
    source_roots = tuple(source_roots)
    test_roots = tuple(test_roots)
    configured_roots = (*source_roots, *test_roots)
    entries, gaps = _index_entries(root)
    scoped_entries = {
        path: mode
        for path, mode in entries.items()
        if path.endswith(".py")
        and "__pycache__" not in PurePosixPath(path).parts
        and _in_scope(path, configured_roots)
    }
    physical = _physical_python_paths(root, configured_roots)
    tracked = set(scoped_entries)
    untracked = sorted(physical - tracked)
    missing = sorted(path for path in tracked if not (root / path).exists())
    symlinks = sorted(path for path in tracked | physical if _has_symlink(root, path))
    non_regular = sorted(
        path
        for path, mode in scoped_entries.items()
        if path not in symlinks
        and (
            mode not in {"100644", "100755"}
            or (path not in missing and not (root / path).is_file())
        )
    )
    test_entries = sorted(path for path in tracked if _in_scope(path, test_roots))
    configured_tests: list[str] = []
    misnamed: list[str] = []
    for path in test_entries:
        relative = PurePosixPath(path)
        if relative.name == "__init__.py":
            continue
        if relative.match("test_*.py"):
            configured_tests.append(path)
        elif relative.name not in {"conftest.py", "fixtures.py"}:
            misnamed.append(path)
    for label, paths in (
        ("untracked", untracked),
        ("missing", missing),
        ("symlink", symlinks),
        ("non_regular", non_regular),
        ("test_misnamed", misnamed),
    ):
        if paths:
            gaps.append(f"quality_inventory_{label}:{','.join(paths)}")
    if not configured_tests:
        gaps.append("quality_inventory_test_empty")
    invalid = {*missing, *symlinks, *non_regular}
    paths = tuple(root / path for path in sorted(tracked - invalid))
    return RepositoryInventory(paths, tuple(sorted(gaps)))


def _docstring_expressions(tree: ast.Module) -> Iterable[ast.Expr]:
    """Yield syntax nodes that carry module, class, or function docstrings."""
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, *_DEFINITION_TYPES))
            and ast.get_docstring(node, clean=False) is not None
        ):
            yield node.body[0]


def _effective_line_numbers(
    source: bytes, tree: ast.Module, tokens: Iterable[tokenize.TokenInfo]
) -> set[int]:
    """Select code lines after removing only comment and docstring spans."""
    lines = source.splitlines()
    spans = [
        ((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset))
        for node in _docstring_expressions(tree)
        if node.end_lineno is not None and node.end_col_offset is not None
    ]
    for token in tokens:
        if token.type == tokenize.COMMENT:
            number, column = token.start
            offset = len(lines[number - 1].decode()[:column].encode())
            spans.append(((number, offset), (number, len(lines[number - 1]))))
    for (start_line, start_column), (end_line, end_column) in sorted(spans, reverse=True):
        for number in range(start_line, end_line + 1):
            line = lines[number - 1]
            first = start_column if number == start_line else 0
            last = end_column if number == end_line else len(line)
            lines[number - 1] = line[:first] + b" " * (last - first) + line[last:]
    return {number for number, line in enumerate(lines, 1) if line.strip()}


def _nesting_depth(node: ast.AST) -> int:
    """Measure control blocks with sibling elif clauses at their own indentation."""
    control = (
        ast.AsyncFor,
        ast.AsyncWith,
        ast.For,
        ast.If,
        ast.Match,
        ast.Try,
        ast.TryStar,
        ast.While,
        ast.With,
    )

    def visit(current: ast.AST, depth: int) -> int:
        if current is not node and isinstance(current, _DEFINITION_TYPES):
            return depth
        next_depth = depth + isinstance(current, control)
        deepest = next_depth
        for child in ast.iter_child_nodes(current):
            child_depth = next_depth
            if (
                isinstance(current, ast.If)
                and isinstance(child, ast.If)
                and child in current.orelse
                and child.col_offset == current.col_offset
            ):
                child_depth = depth
            deepest = max(deepest, visit(child, child_depth))
        return deepest

    return visit(node, 0)


def _source_structure(
    tree: ast.Module, effective_lines: set[int], tokens: Iterable[tokenize.TokenInfo]
) -> tuple[int, int]:
    """Measure function ELOC and independent module, class and function control depth."""
    decorator_starts = tuple(
        token.start for token in tokens if token.type == tokenize.OP and token.string == "@"
    )
    function_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.end_lineno is None:
            continue
        start = node.lineno
        if node.decorator_list:
            first = node.decorator_list[0]
            start = decorator_starts[
                bisect_right(decorator_starts, (first.lineno, first.col_offset)) - 1
            ][0]
        function_lines.append(sum(start <= line <= node.end_lineno for line in effective_lines))
    depths = (
        _nesting_depth(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, *_DEFINITION_TYPES))
    )
    return max(function_lines, default=0), max(depths, default=0)


def _logical_statements(tree: ast.Module) -> int:
    """Count syntax statements, excluding only actual documentation nodes."""
    return sum(isinstance(node, ast.stmt) for node in ast.walk(tree)) - sum(
        1 for _ in _docstring_expressions(tree)
    )


def audit_paths(
    root: Path,
    paths: Iterable[Path],
    *,
    max_nesting_depth: int,
) -> tuple[list[str], list[SourceMetrics]]:
    """Enforce the declared control-depth boundary and report exact source metrics."""
    gaps: list[str] = []
    inventory: list[SourceMetrics] = []
    selected = sorted(set(paths))
    for path in selected:
        source = path.read_bytes()
        tree = ast.parse(source, filename=str(path))
        tokens = tuple(tokenize.tokenize(BytesIO(source).readline))
        relative = path.relative_to(root).as_posix()
        logical = _logical_statements(tree)
        effective_lines = _effective_line_numbers(source, tree, tokens)
        function_eloc, nesting_depth = _source_structure(tree, effective_lines, tokens)
        if nesting_depth > max_nesting_depth:
            gaps.append(
                f"quality_nesting_depth_exceeds_limit:{relative}:"
                f"{nesting_depth}>{max_nesting_depth}"
            )
        inventory.append(
            {
                "path": relative,
                "logical_statements": logical,
                "effective_lines": len(effective_lines),
                "max_function_lines": function_eloc,
                "max_nesting_depth": nesting_depth,
            }
        )
    return sorted(gaps), inventory


def _string_list(policy: Mapping[str, object], key: str, errors: list[str]) -> list[str]:
    value = policy.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        errors.append(f"quality_policy_{key}_must_be_nonempty_string_list")
        return []
    return [item for item in value if isinstance(item, str)]


def audit() -> AuditReport:
    """Return one deterministic quality report for CI and local verification."""
    policy = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    config = tomllib.loads(PROJECT.read_text(encoding="utf-8"))
    tool = config.get("tool", {})
    project = config.get("project", {})
    project_name = project.get("name") if isinstance(project, dict) else None
    repository = tool.get(project_name, {}) if isinstance(project_name, str) else {}
    policy_errors: list[str] = []
    if not isinstance(project, dict) or project.get("requires-python") != supported_requirement(
        ROOT / ".config/ci/python-versions"
    ):
        policy_errors.append("requires_python_must_match_supported_matrix")
    hatch_version = tool.get("hatch", {}).get("version", {})
    if project.get("dynamic") != ["version"] or hatch_version.get("path") != "VERSION":
        policy_errors.append("version_source_must_remain_VERSION")
    if repository.get("distribution") != "native-executable" or "build-system" not in config:
        policy_errors.append("distribution_must_be_native_executable")
    source_roots = _string_list(policy, "source_roots", policy_errors)
    test_roots = _string_list(policy, "test_roots", policy_errors)
    configuration_roots = _string_list(policy, "root_configuration_modules", policy_errors)
    nesting = policy.get("control_nesting")
    max_depth = nesting.get("max_depth") if isinstance(nesting, dict) else None
    if type(max_depth) is not int or max_depth < 1:
        policy_errors.append("quality_policy_control_nesting_max_depth_must_be_positive_integer")
        max_depth = 0
    repository_inventory = _repository_inventory(
        ROOT, [*source_roots, *configuration_roots], test_roots
    )
    gaps, inventory = audit_paths(ROOT, repository_inventory.paths, max_nesting_depth=max_depth)
    configured_paths = {
        "source_roots": source_roots,
        "test_roots": test_roots,
        "root_configuration_modules": configuration_roots,
    }
    for key, values in configured_paths.items():
        for value in values:
            matches = ROOT.glob(value) if any(char in value for char in "*?[") else (ROOT / value,)
            if not any(path.exists() for path in matches):
                policy_errors.append(f"quality_policy_missing_path:{key}:{value}")
    all_gaps = sorted(
        [
            *policy_errors,
            *repository_inventory.gaps,
            *gaps,
            *architecture_gaps(ROOT, policy),
            *semantic_name_gaps(ROOT),
        ]
    )
    return {
        "ok": not all_gaps,
        "gaps": all_gaps,
        "policy_errors": sorted(policy_errors),
        "files": inventory,
        "configured_paths": configured_paths,
    }


def _command() -> None:
    """Audit the repository quietly, emitting diagnostics only for gaps."""
    report = audit()
    if not report["ok"]:
        print(json.dumps(report, sort_keys=True))
        raise SystemExit(1)


def main(argv: Iterable[str] = ()) -> None:
    """Run the repository audit through the repository's single parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(tuple(argv))


if __name__ == "__main__":
    main(sys.argv[1:])
