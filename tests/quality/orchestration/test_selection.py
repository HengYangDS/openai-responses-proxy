"""Every marker of the test configuration reaches a gate session that runs its cases."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from typing import TYPE_CHECKING

import nox
import pytest

from tests.forge.workflows.fixtures import _load_yaml
from tests.quality.fixtures import ROOT
from tools.ci import project
from tools.quality import governance

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType

    from pytest_mock import MockerFixture

CONFIGURATION = ROOT / governance.PYTEST_CONFIG
TASKS = ROOT / ".config/mise/config.toml"
SESSION = re.compile(r'nox -s "?([a-z_]+)')
type Selection = tuple[str | None, tuple[str, ...]]


def _configuration() -> tuple[frozenset[str], tuple[str, ...]]:
    """Read the declared marker names and the test roots from their one owner."""
    declared = tomllib.loads(CONFIGURATION.read_text(encoding="utf-8"))["pytest"]
    names = frozenset(str(marker).partition(":")[0] for marker in declared["markers"])
    return names, tuple(str(root) for root in declared["testpaths"])


def _carriers(names: frozenset[str], roots: tuple[str, ...]) -> dict[str, frozenset[str]]:
    """Map each test module to the declared markers that its source applies."""
    carriers = {}
    for path in sorted(module for root in roots for module in (ROOT / root).rglob("*.py")):
        applied = {
            node.attr
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "pytest.mark"
        }
        if applied & names:
            carriers[path.relative_to(ROOT).as_posix()] = frozenset(applied & names)
    return carriers


def _task_sessions(task: str) -> set[str]:
    """Read the sessions that one Mise task runs."""
    tasks = tomllib.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    return set(SESSION.findall(str(tasks[task]["run"])))


def _workflow_sessions() -> set[str]:
    """Read the sessions that the generated Forge workflows run."""
    sessions: set[str] = set()
    pending: list[object] = [
        _load_yaml(item.path) for item in project.PROJECTIONS if item.output_format == "yaml"
    ]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            sessions.update(SESSION.findall(value))
        elif isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return sessions


def _recorder(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> Callable[[set[str]], list[tuple[str, ...]]]:
    """Replace product supply once; return a runner that keeps sessions' pytest commands."""
    issued: list[tuple[str, ...]] = []

    def record(*command: object, **_options: object) -> str:
        issued.append(tuple(str(part) for part in command))
        return ""

    session = mocker.Mock(posargs=[], python=nox_configuration.MIN_PYTHON, bin=str(tmp_path))
    session.name = "quality"
    session.create_tmp.return_value = str(tmp_path)
    session.run.side_effect = record
    executable = tmp_path / "proxy"
    for name, value in (
        ("_prepare_product_session", tmp_path),
        ("_installed_executable", executable),
        ("_build_native_candidate", (tmp_path, tmp_path / "bundle", executable)),
        ("_assert_release_runtime", None),
        ("_required_file", tmp_path / "input"),
    ):
        mocker.patch.object(nox_configuration, name, return_value=value)
    mocker.patch.object(nox_configuration.construction, "pack")
    mocker.patch.object(nox_configuration.performance_measurement, "measure")
    verification = nox_configuration.verification
    mocker.patch.object(
        verification, "materialize_published_bundle", return_value=(tmp_path, executable)
    )
    mocker.patch.object(
        verification, "accept", side_effect=lambda _session, command, **_options: record(*command)
    )
    # The quality session runs its observer cases only when native output is selected.
    monkeypatch.setenv("ETHOS_NATIVE_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv(
        nox_configuration.product_identity.environment_name("CURRENT_RELEASE_ASSETS"), str(tmp_path)
    )
    registered = nox.registry.get()

    def run(names: set[str]) -> list[tuple[str, ...]]:
        """Run the named sessions and every session that they start."""
        issued.clear()
        session.notify.reset_mock()
        pending, ran = sorted(names), set()
        while pending:
            # A session of one interpreter is named after its registered session.
            name = pending.pop().partition("-")[0]
            if name not in ran:
                ran.add(name)
                registered[name](session)
                pending.extend(str(call.args[0]) for call in session.notify.call_args_list)
        return [command for command in issued if "pytest" in command]

    return run


def _selection(command: tuple[str, ...], roots: tuple[str, ...]) -> Selection:
    """Read one pytest command's marker expression and the test paths that narrow it."""
    arguments = command[command.index("pytest") + 1 :]
    expression = arguments[arguments.index("-m") + 1] if "-m" in arguments else None
    paths = tuple(
        argument
        for argument in arguments
        if any(argument == root or argument.startswith(f"{root}/") for root in roots)
    )
    return expression, paths


def _admitted(expression: str, names: frozenset[str], work: Path) -> frozenset[str]:
    """Ask pytest which declared markers one marker expression selects."""
    work.mkdir(exist_ok=True)
    probe = work / "test_probe.py"
    cases = (f"\n@pytest.mark.{name}\ndef test_{name}():\n    pass\n" for name in sorted(names))
    probe.write_text("import pytest\n" + "".join(cases), encoding="utf-8")
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--rootdir",
            str(work),
            "-c",
            str(CONFIGURATION),
            "-m",
            expression,
            str(probe),
        ),
        cwd=work,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode in {pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED}, (
        result.stdout + result.stderr
    )
    return frozenset(name for name in names if f"::test_{name}\n" in result.stdout)


def _unselected(
    names: frozenset[str],
    carriers: dict[str, frozenset[str]],
    selections: set[Selection],
    admitted: dict[str, frozenset[str]],
) -> dict[str, list[str]]:
    """Name each marker that no selection runs, with the modules that are left out."""
    missing = {}
    for name in sorted(names):
        reached = [
            paths
            for expression, paths in selections
            if expression is None or name in admitted[expression]
        ]
        left_out = sorted(
            module
            for module, applied in carriers.items()
            if name in applied
            and not any(
                not paths or any(module == path or module.startswith(f"{path}/") for path in paths)
                for paths in reached
            )
        )
        if left_out or not reached:
            missing[name] = left_out
    return missing


def test_every_declared_marker_is_selected_by_a_gate_session_that_runs_its_cases(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    """A marker that one gate deselects needs another gate that selects it.

    The gates are the session of the Mise task ``check`` with the sessions that it starts,
    every session that a generated Forge workflow runs, and the governance graph.
    """
    names, roots = _configuration()
    carriers = _carriers(names, roots)
    run = _recorder(tmp_path, mocker, monkeypatch, nox_configuration)
    mocker.patch.object(governance, "_tracked_current", return_value=("README.md",))
    graph = [command for command in governance._commands(online_links=False) if "pytest" in command]
    gates = run(_task_sessions("check") | _workflow_sessions())
    selections = {_selection(command, roots) for command in (*gates, *graph)}
    loop = {_selection(command, roots) for command in run(_task_sessions("quick"))}
    admitted = {
        expression: _admitted(expression, names, tmp_path / "probe")
        for expression, _paths in selections | loop
        if expression is not None
    }

    assert _unselected(names, carriers, selections, admitted) == {}

    only_loop = next(
        path for _expression, paths in loop - selections for path in paths if path.endswith(".py")
    )
    for name in names:
        # Withdrawing the gates that reach a marker reports every module that carries it.
        withdrawn: set[Selection] = {
            (expression, paths)
            for expression, paths in selections
            if expression is not None and name not in admitted[expression]
        }
        assert _unselected(frozenset({name}), carriers, withdrawn, admitted) == {
            name: sorted(module for module, applied in carriers.items() if name in applied)
        }
        # The editing loop is no gate: a module that it alone selects is still reported.
        forced = {only_loop: frozenset({name})}
        assert _unselected(frozenset({name}), forced, withdrawn, admitted) == {name: [only_loop]}
        assert _unselected(frozenset({name}), forced, withdrawn | loop, admitted) == {}
