"""Every marker of the test configuration reaches a gate session that runs its cases."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import nox
import pytest

from tests.forge.workflows.fixtures import _load_yaml
from tests.quality.fixtures import ROOT
from tools.ci import project
from tools.quality import governance

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from types import ModuleType

    from pytest_mock import MockerFixture

CONFIGURATION = ROOT / governance.PYTEST_CONFIG
TASKS = ROOT / ".config/mise/config.toml"
PROFILE = ROOT / ".ethos/profile.toml"
SESSION = re.compile(r'nox -s "?([a-z_]+)')
# The options of the gates' pytest commands that the reader models. Of these only the marker
# expression narrows the selection; two must name this checkout and its test configuration.
FLAGS = frozenset({"-q"})
VALUED = frozenset({"-m", "-c", "--rootdir", "-p", "--junitxml", "--report-log"}) | {
    "--ethos-runtime-output",
    "--ethos-native-session",
}
BOUND = {"-c": CONFIGURATION, "--rootdir": ROOT}
BASE = ("python", "-m", "pytest", "-q", "--rootdir", str(ROOT), "-c", str(CONFIGURATION))
# One pytest collection for each marker expression and its own probe module.
PROBE = """
import sys
import pytest
configuration, *pairs = sys.argv[1:]
collected = (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)
for expression, probe in zip(pairs[::2], pairs[1::2]):
    selected = ("--collect-only", "-q", "--rootdir", ".", "-c", configuration, "-m", expression)
    if pytest.main([*selected, probe]) not in collected:
        sys.exit(1)
"""
type Command = tuple[str, ...]
type Selection = tuple[str | None, tuple[str, ...]]
type Bearing = dict[Path, frozenset[str]]


def _configuration() -> tuple[frozenset[str], tuple[str, ...]]:
    """Read the declared marker names and the test roots from their one owner."""
    declared = tomllib.loads(CONFIGURATION.read_text(encoding="utf-8"))["pytest"]
    names = frozenset(str(marker).partition(":")[0] for marker in declared["markers"])
    return names, tuple(str(root) for root in declared["testpaths"])


def _is_mark(node: ast.AST) -> bool:
    """Recognize the expression ``pytest.mark``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "mark"
        and isinstance(node.value, ast.Name)
        and node.value.id == "pytest"
    )


def _mark_uses(nodes: Iterable[ast.AST]) -> tuple[set[str], list[tuple[int, str]]]:
    """Read the names applied as ``pytest.mark.<name>``, and each other way to a mark by line.

    The nodes come from ``ast.walk``, which yields a parent before its children.
    """
    literal: set[str] = set()
    read: set[int] = set()
    other: list[tuple[int, str]] = []
    for node in nodes:
        if isinstance(node, ast.Attribute) and _is_mark(node.value):
            read.add(id(node.value))
            literal.add(node.attr)
            if node.attr.startswith("_"):
                other.append((node.lineno, f"reads pytest.mark.{node.attr}"))
        elif isinstance(node, ast.Attribute) and node.attr == "add_marker":
            other.append((node.lineno, "calls add_marker"))
        elif isinstance(node, ast.Attribute) and _is_mark(node) and id(node) not in read:
            other.append((node.lineno, "uses pytest.mark other than as pytest.mark.<name>"))
        elif isinstance(node, ast.Import | ast.ImportFrom) and _rebinds(node):
            other.append((node.lineno, "binds pytest.mark to another name"))
    return literal, other


def _rebinds(statement: ast.Import | ast.ImportFrom) -> bool:
    """Recognize an import that reaches ``pytest.mark`` under another name."""
    if isinstance(statement, ast.Import):
        return any(alias.name == "pytest" and alias.asname for alias in statement.names)
    module = statement.module or ""
    return module.startswith("_pytest.mark") or (
        module == "pytest" and any(alias.name in {"mark", "*"} for alias in statement.names)
    )


def _module_file(name: str, level: int, importer: Path, root: Path) -> Path | None:
    """Find the repository file of one imported module, if this repository holds it."""
    stem = (importer.parents[level - 1] if level else root).joinpath(*filter(None, name.split(".")))
    return next(
        (path for path in (stem.with_suffix(".py"), stem / "__init__.py") if path.is_file()), None
    )


def _bearing(
    path: Path | None, names: frozenset[str], root: Path, cache: Bearing
) -> frozenset[str]:
    """Name what one module binds at its top level to a declared marker, itself or by import."""
    if path is None:
        return frozenset()
    if path not in cache:
        cache[path] = frozenset()  # An import cycle ends here.
        bound: set[str] = set()
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            literal, other = _mark_uses(ast.walk(statement))
            if isinstance(statement, ast.Import | ast.ImportFrom):
                bound |= _imported(statement, path, names, root, cache)[0]
            elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bound |= {statement.name} if literal & names or other else set()
            elif literal & names or other:
                stored = (node for node in ast.walk(statement) if isinstance(node, ast.Name))
                bound |= {node.id for node in stored if isinstance(node.ctx, ast.Store)}
        cache[path] = frozenset(bound)
    return cache[path]


def _imported(
    statement: ast.Import | ast.ImportFrom,
    importer: Path,
    names: frozenset[str],
    root: Path,
    cache: Bearing,
) -> tuple[set[str], dict[str, frozenset[str]]]:
    """Name what one import binds to a mark object, and each module it binds that holds one."""
    direct: set[str] = set()
    modules: dict[str, frozenset[str]] = {}
    package = (statement.module or "") if isinstance(statement, ast.ImportFrom) else ""
    level = statement.level if isinstance(statement, ast.ImportFrom) else 0
    source = frozenset[str]()
    if isinstance(statement, ast.ImportFrom):
        source = _bearing(_module_file(package, level, importer, root), names, root, cache)
    for alias in statement.names:
        local = alias.asname or alias.name
        if alias.name == "*":
            direct |= source
        elif alias.name in source:
            direct.add(local)
        else:
            module = _module_file(f"{package}.{alias.name}", level, importer, root)
            modules[local] = _bearing(module, names, root, cache)
    return direct, {local: held for local, held in modules.items() if held}


def _dotted(node: ast.AST) -> str:
    """Spell a name or an attribute chain of names; anything else is empty."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)):
        return f"{base}.{node.attr}"
    return ""


def _carriers(
    names: frozenset[str], roots: tuple[str, ...], root: Path = ROOT
) -> tuple[dict[str, frozenset[str]], list[str]]:
    """Map each test module to the declared markers that it applies as ``pytest.mark.<name>``.

    A module applies a declared marker only in that literal form. Every other way to a mark
    is listed with its module and line, because this scan would not see what it applies.
    """
    carriers: dict[str, frozenset[str]] = {}
    unreadable: list[str] = []
    cache: Bearing = {}
    reason = "imports a mark object"
    for path in sorted(module for item in roots for module in (root / item).rglob("*.py")):
        nodes = tuple(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
        literal, other = _mark_uses(nodes)
        modules: dict[str, frozenset[str]] = {}
        for node in nodes:
            if isinstance(node, ast.Import | ast.ImportFrom):
                direct, held = _imported(node, path, names, root, cache)
                modules |= held
                other.extend([(node.lineno, reason)] * bool(direct))
        # Only a module that imports one that holds a mark object is read for its use.
        attributes = (node for node in nodes if modules and isinstance(node, ast.Attribute))
        other.extend(
            (node.lineno, reason)
            for node in attributes
            if node.attr in modules.get(_dotted(node.value), ())
        )
        module = path.relative_to(root).as_posix()
        unreadable.extend(f"{module}:{line}: {text}" for line, text in sorted(other))
        if literal & names:
            carriers[module] = frozenset(literal & names)
    return carriers, unreadable


@pytest.fixture(scope="module")
def scan() -> tuple[frozenset[str], dict[str, frozenset[str]], list[str]]:
    """Read the declared markers and scan the test roots once for this module."""
    names, roots = _configuration()
    return (names, *_carriers(names, roots))


def _task_sessions(task: str) -> set[str]:
    """Read the sessions that one Mise task runs."""
    tasks = tomllib.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    return set(SESSION.findall(str(tasks[task]["run"])))


def _proof_sessions() -> set[str]:
    """Read the sessions that the proof's gate commands run."""
    gates = tomllib.loads(PROFILE.read_text(encoding="utf-8"))["proof"]["gates"]
    return {
        name
        for gate in gates
        for name in SESSION.findall(" ".join(str(part) for part in gate["command"]))
    }


def _workflow_texts() -> list[str]:
    """Read every text of the generated Forge workflows."""
    texts: list[str] = []
    pending: list[object] = [
        _load_yaml(item.path) for item in project.PROJECTIONS if item.output_format == "yaml"
    ]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return texts


def _sessions(texts: list[str]) -> set[str]:
    """Read the sessions that the given command texts run."""
    return {name for text in texts for name in SESSION.findall(text)}


def _recorder(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> Callable[..., list[Command]]:
    """Replace product supply once; return a runner that keeps the sessions' commands."""
    issued: list[Command] = []

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
    monkeypatch.setenv(
        nox_configuration.product_identity.environment_name("CURRENT_RELEASE_ASSETS"), str(tmp_path)
    )
    registered = nox.registry.get()

    def run(names: set[str], *, proof: bool = False) -> list[Command]:
        """Run the named sessions and every session that they start, as the proof or not."""
        issued.clear()
        session.notify.reset_mock()
        if proof:  # Only the proof selects native output, and with it the observer cases.
            monkeypatch.setenv("ETHOS_NATIVE_OUTPUT_DIR", str(tmp_path))
        else:
            monkeypatch.delenv("ETHOS_NATIVE_OUTPUT_DIR", raising=False)
        pending, ran = sorted(names), set()
        while pending:
            # A session of one interpreter is named after its registered session.
            name = pending.pop().partition("-")[0]
            if name not in ran:
                ran.add(name)
                registered[name](session)
                pending.extend(str(call.args[0]) for call in session.notify.call_args_list)
        return list(issued)

    return run


def _gate_selections(
    run: Callable[..., list[Command]], sessions: set[str], proof: set[str], texts: list[str]
) -> set[Selection]:
    """Read what the gates select; the governance graph counts only where a gate starts it."""
    issued = [*run(sessions), *run(proof, proof=True)]
    module = governance.__name__
    if any(module in command and "--fix" not in command for command in issued) or any(
        f"-m {module}" in text for text in texts
    ):
        issued.extend(governance._commands(online_links=False))
    return {_selection(command) for command in issued if "pytest" in command}


def _selection(command: Command) -> Selection:
    """Read one gate command's marker expression and the paths that narrow it.

    The reader models the options that the gates pass today. Any other argument fails by
    name, because an option that it does not model could narrow the selection unseen.
    """
    arguments = iter(command[command.index("pytest") + 1 :])
    expression: str | None = None
    paths: list[str] = []
    for argument in arguments:
        value = next(arguments) if argument in VALUED else ""
        target = (ROOT / (value or argument)).resolve()
        if argument == "-m":
            expression = value
        elif argument in BOUND:
            assert target == BOUND[argument], f"unmodelled pytest argument: {argument} {value}"
        elif argument not in VALUED | FLAGS:
            path = not argument.startswith("-") and target.exists() and target.is_relative_to(ROOT)
            assert path, f"unmodelled pytest argument: {argument}"
            paths.append(target.relative_to(ROOT).as_posix())
    return expression, tuple(paths)


def _admitted(
    expressions: set[str], names: frozenset[str], work: Path
) -> dict[str, frozenset[str]]:
    """Ask pytest, in one child, which declared markers each marker expression selects."""
    work.mkdir(exist_ok=True)
    cases = (f"\n@pytest.mark.{name}\ndef test_{name}():\n    pass\n" for name in sorted(names))
    source = "import pytest\n" + "".join(cases)
    probes = {expression: f"test_probe_{index}.py" for index, expression in enumerate(expressions)}
    for probe in probes.values():
        (work / probe).write_text(source, encoding="utf-8")
    result = subprocess.run(
        (
            sys.executable,
            "-c",
            PROBE,
            str(CONFIGURATION),
            *(part for expression, probe in probes.items() for part in (expression, probe)),
        ),
        cwd=work,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        expression: frozenset(name for name in names if f"{probe}::test_{name}\n" in result.stdout)
        for expression, probe in probes.items()
    }


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


def _carried(
    carriers: dict[str, frozenset[str]], admitted: dict[str, frozenset[str]], only: set[Selection]
) -> dict[str, list[str]]:
    """Name every carrier of each marker that the given selections admit."""
    reached = {
        name
        for expression, _paths in only
        if expression is not None
        for name in admitted[expression]
    }
    return {
        name: sorted(module for module, applied in carriers.items() if name in applied)
        for name in sorted(reached)
    }


def test_every_declared_marker_is_selected_by_a_gate_session_that_runs_its_cases(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
    scan: tuple[frozenset[str], dict[str, frozenset[str]], list[str]],
) -> None:
    """A marker that one gate deselects needs another gate that selects it.

    The gates are the session of the Mise task ``check`` with the sessions that it starts,
    every session that a generated Forge workflow runs, the sessions of the proof's gate
    commands, and the governance graph where one of those starts it.
    """
    names, carriers, unreadable = scan
    assert unreadable == []
    run = _recorder(tmp_path, mocker, monkeypatch, nox_configuration)
    mocker.patch.object(governance, "_tracked_current", return_value=("README.md",))
    texts = _workflow_texts()
    sessions, proof = _task_sessions("check") | _sessions(texts), _proof_sessions()
    selections = _gate_selections(run, sessions, proof, texts)
    unproved = _gate_selections(run, sessions, set(), texts)
    ungoverned = _gate_selections(run, _sessions(texts), proof, [])
    loop = {_selection(command) for command in run(_task_sessions("quick")) if "pytest" in command}
    expressions = {expression for expression, _paths in selections | loop if expression is not None}
    admitted = _admitted(expressions, names, tmp_path / "probe")

    assert _unselected(names, carriers, selections, admitted) == {}

    # Without the proof's sessions, or without a gate that starts the governance graph, the
    # markers that only they select are reported with every module that carries them.
    for partial in (unproved, ungoverned):
        assert selections - partial
        assert _unselected(names, carriers, partial, admitted) == _carried(
            carriers, admitted, selections - partial
        )
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


def test_a_gate_that_runs_before_the_host_ticket_runs_no_test_case(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    """ETHOS runs every gate that is neither a test nor a package gate before admission."""
    run = _recorder(tmp_path, mocker, monkeypatch, nox_configuration)
    gates = tomllib.loads(PROFILE.read_text(encoding="utf-8"))["proof"]["gates"]
    ready = [gate for gate in gates if gate["kind"] not in {"test", "package"}]
    admitted = [gate for gate in gates if gate["kind"] in {"test", "package"}]

    def cases(gate: Mapping[str, object]) -> list[Command]:
        sessions = set(SESSION.findall(" ".join(str(part) for part in gate["command"])))
        return [command for command in run(sessions, proof=True) if "pytest" in command]

    assert ready
    assert {gate["id"]: cases(gate) for gate in ready} == {gate["id"]: [] for gate in ready}
    assert any(cases(gate) for gate in admitted)


@pytest.mark.parametrize(
    "spelling", ["{}", "./{}", f"{ROOT}/{{}}"], ids=["relative", "dotted", "absolute"]
)
def test_a_gate_path_narrows_the_selection_however_it_is_spelled(
    spelling: str, scan: tuple[frozenset[str], dict[str, frozenset[str]], list[str]]
) -> None:
    names, carriers, _unreadable = scan
    directory = Path(min(carriers)).parent.as_posix()

    selection = _selection((*BASE, spelling.format(directory)))

    assert selection == (None, (directory,))
    outside = {module for module in carriers if not module.startswith(f"{directory}/")}
    assert _unselected(names, carriers, {selection}, {}) == {
        name: sorted(module for module in outside if name in carriers[module])
        for name in sorted(names)
        if any(name in carriers[module] for module in outside)
    }


@pytest.mark.parametrize(
    ("option", "arguments"),
    [
        ("-k", ("tests/quality/orchestration/test_observation.py", "-k", "no_such_case")),
        ("--ignore=", ("--ignore=tests/ci",)),
        ("--ignore-glob=", ("--ignore-glob=tests/c*",)),
        ("--deselect", ("--deselect", "tests/ci/test_projection.py::test_case")),
        ("tests/ci/test_projection.py::test_case", ("tests/ci/test_projection.py::test_case",)),
        ("-c", ("-c", "pyproject.toml")),
    ],
)
def test_a_gate_argument_that_the_reader_does_not_model_fails_by_name(
    option: str, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(AssertionError, match=re.escape(f"unmodelled pytest argument: {option}")):
        _selection((*BASE, *arguments))


@pytest.mark.parametrize(
    ("source", "line", "reason"),
    [
        (
            "import pytest\nnative = pytest.mark\n\n\n@native.forced\ndef test_case(): ...\n",
            2,
            "uses pytest.mark other than as pytest.mark.<name>",
        ),
        (
            'import pytest\n\n\n@getattr(pytest.mark, "forced")\ndef test_case(): ...\n',
            4,
            "uses pytest.mark other than as pytest.mark.<name>",
        ),
        (
            'def test_case(request):\n    request.node.add_marker("forced")\n',
            2,
            "calls add_marker",
        ),
        (
            "from tools.marks import forced\n\n\n@forced\ndef test_case(): ...\n",
            1,
            "imports a mark object",
        ),
        (
            "from tools import marks\n\n\n@marks.forced\ndef test_case(): ...\n",
            4,
            "imports a mark object",
        ),
        (
            "from pytest import mark\n\n\n@mark.forced\ndef test_case(): ...\n",
            1,
            "binds pytest.mark to another name",
        ),
    ],
    ids=["alias", "getattr", "add_marker", "imported-object", "imported-module", "from-pytest"],
)
def test_a_marker_applied_outside_the_literal_form_fails_by_module_and_line(
    tmp_path: Path, source: str, line: int, reason: str
) -> None:
    for relative, text in (
        ("tools/marks.py", "import pytest\n\nforced = pytest.mark.forced\n"),
        ("tests/test_forced.py", source),
        ("tests/test_literal.py", "import pytest\n\n\n@pytest.mark.forced\ndef test_case(): ...\n"),
    ):
        (tmp_path / relative).parent.mkdir(exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")

    carriers, unreadable = _carriers(frozenset({"forced"}), ("tests",), tmp_path)

    assert carriers == {"tests/test_literal.py": frozenset({"forced"})}
    assert unreadable == [f"tests/test_forced.py:{line}: {reason}"]
