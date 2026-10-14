"""Verification session ordering and failure propagation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from nox.command import CommandFailed
from nox.sessions import Session
from nox.virtualenv import PassthroughEnv
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT


def test_nox_console_discovers_the_repository_sessions_without_pythonpath() -> None:
    result = subprocess.run(
        ("nox", "--list", "--json"),
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    sessions = {item["session"] for item in json.loads(result.stdout)}
    assert {"quality", "tests-3.12", "tests-3.13", "tests-3.14"} <= sessions


def test_native_report_selection_refuses_relative_output_before_static_writes(
    mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    session = mocker.Mock()
    session.error.side_effect = ValueError("native output directory is unavailable")
    mocker.patch.dict(os.environ, {"ETHOS_NATIVE_OUTPUT_DIR": "."})

    with pytest.raises(ValueError, match="native output directory is unavailable"):
        nox_configuration.python_quality.static(session, environment={}, minimum_python="3.12")

    session.run.assert_not_called()


@pytest.mark.parametrize("changed", [False, True])
def test_native_helper_supply_checks_the_supplied_wheel_before_install(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
    changed: bool,
) -> None:
    """The attempt selects the observer; this session only verifies its exact bytes."""
    wheel = tmp_path / "original wheel.whl"
    wheel.write_bytes(b"original immutable wheel fixture")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    requirement = f"ethos @ {wheel.as_uri()}#sha256={digest}"
    if changed:
        wheel.write_bytes(b"changed wheel fixture")
    mocker.patch.dict(
        os.environ,
        {
            "ETHOS_NATIVE_OUTPUT_DIR": str(tmp_path),
            "ETHOS_NATIVE_OBSERVER_REQUIREMENT": requirement,
        },
    )
    session = mocker.Mock()
    session.error.side_effect = ValueError("native helper wheel unavailable")

    if changed:
        with pytest.raises(ValueError, match="native helper wheel unavailable"):
            nox_configuration.python_quality.install_native_helpers(session)
        session.install.assert_not_called()
    else:
        nox_configuration.python_quality.install_native_helpers(session)
        session.install.assert_called_once_with(
            "--no-deps",
            requirement,
            env={"PYTHONNOUSERSITE": "1", "UV_NO_PROGRESS": "1"},
        )


@pytest.mark.parametrize(
    "requirement",
    [
        "",
        "ethos @ file:///wheel.whl",
        "ethos @ https://example.invalid/wheel.whl#sha256=" + "a" * 64,
        "ethos @ file:///wheel.whl#sha256=not-a-digest",
        "ethos @ file:///wheel.whl#sha512=" + "a" * 64,
    ],
)
def test_native_helper_supply_refuses_unsupplied_or_invalid_requirements(
    requirement: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """An absent, remote, unhashed or malformed supply cannot reach installation."""
    mocker.patch.dict(
        os.environ,
        {
            "ETHOS_NATIVE_OUTPUT_DIR": str(tmp_path),
            "ETHOS_NATIVE_OBSERVER_REQUIREMENT": requirement,
        },
    )
    session = mocker.Mock()
    session.error.side_effect = ValueError("native helper wheel unavailable")

    with pytest.raises(ValueError, match="native helper wheel unavailable"):
        nox_configuration.python_quality.install_native_helpers(session)
    session.install.assert_not_called()


@pytest.mark.parametrize(("platform_name", "ruff_name"), [("posix", "ruff"), ("nt", "ruff.exe")])
def test_quality_reports_bind_checkers_and_preserve_outer_attempt(
    platform_name: str,
    ruff_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """This interpreter has no ETHOS, so a parent import would fail the session."""
    output = tmp_path / "reports"
    output.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    session = mocker.Mock(python="3.12", bin=str(tmp_path))
    session.name = "quality"
    session.env = {}
    session.create_tmp.return_value = str(work)
    mocker.patch.dict(os.environ, {"ETHOS_NATIVE_OUTPUT_DIR": str(output)})
    mocker.patch.object(
        nox_configuration.python_quality,
        "os",
        SimpleNamespace(name=platform_name, environ=os.environ),
    )
    for name in ("_install_tools", "_build_wheel", "_install_wheel", "_assert_installed_product"):
        mocker.patch.object(nox_configuration, name)

    nox_configuration.quality(session)

    custody = [
        call for call in session.run.call_args_list if "ethos.domain.quality.nox" in call.args
    ]
    assert len(custody) == 1
    checker = custody[0].args
    assert checker[:6] == (
        "python",
        "-B",
        "-I",
        "-m",
        "ethos.domain.quality.nox",
        "run",
    )
    assert checker[checker.index("--session") + 1] == "quality-3.12"
    assert checker[checker.index("--runtime-output") + 1] == str(
        output / "quality-ruff-runtime.json"
    )
    assert checker[checker.index("--configuration") + 1] == str(
        nox_configuration.python_quality.RUFF_CONFIG
    )
    ruff = list(checker[checker.index("--") + 1 :])
    assert ruff[0] == str(tmp_path / ruff_name)
    assert ruff[1] == "check"
    assert "--verbose" in ruff
    assert ruff[ruff.index("--output-file") + 1] == str(output / "quality-ruff.json")
    behavior, nested = [call for call in session.run.call_args_list if "pytest" in call.args]
    assert behavior.args[behavior.args.index("--ethos-native-session") + 1] == "quality-3.12"
    assert behavior.args[behavior.args.index("--ethos-runtime-output") + 1] == str(
        output / "quality-runtime.json"
    )
    assert behavior.kwargs["env"]["ETHOS_NATIVE_OUTPUT_DIR"] is None
    assert nested.kwargs["env"]["ETHOS_NATIVE_OUTPUT_DIR"] is None
    assert "--ethos-runtime-output" not in nested.args
    assert any(
        call.args[:2] == ("coverage", "xml")
        and call.args[-1] == str(output / "quality-coverage.xml")
        for call in session.run.call_args_list
    )


def test_native_report_refuses_session_without_concrete_python(
    mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    session = mocker.Mock(python=False)
    session.error.side_effect = ValueError("concrete Python is required")
    with pytest.raises(ValueError, match="concrete Python is required"):
        nox_configuration.python_quality._native_session(session)


@pytest.mark.repository_toolchain
def test_native_behavior_reports_share_one_actual_pytest_attempt(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """The installed ETHOS plugin binds one original pytest attempt and its config."""
    output = tmp_path / "reports"
    output.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "sample.py").write_text("def answer():\n    return 42\n")
    configuration = tmp_path / "pytest.toml"
    configuration.write_text('[pytest]\nfilterwarnings = ["error"]\n')
    (tmp_path / "test_sample.py").write_text(
        "from pathlib import Path\nfrom sample import answer\n\n"
        "def test_answer():\n"
        "    with Path('attempt-count.txt').open('a') as stream:\n"
        "        stream.write('one\\n')\n"
        "    assert answer() == 42\n"
    )
    coverage = tmp_path / "coverage.toml"
    coverage.write_text('[run]\nbranch = true\nsource_pkgs = ["sample"]\n')
    session = mocker.Mock()
    session.name = f"tests-{sys.version_info.major}.{sys.version_info.minor}"
    session.python = f"{sys.version_info.major}.{sys.version_info.minor}"
    session.create_tmp.return_value = str(work)
    mocker.patch.dict(os.environ, {"ETHOS_NATIVE_OUTPUT_DIR": str(output)})
    mocker.patch.object(nox_configuration.python_quality, "ROOTS", ("sample.py", "test_sample.py"))
    mocker.patch.object(nox_configuration.python_quality, "ROOT", tmp_path)
    mocker.patch.object(nox_configuration.python_quality, "COVERAGE_CONFIG", coverage)
    mocker.patch.object(nox_configuration.python_quality, "PYTEST_CONFIG", configuration)
    for name in ("_install_tools", "_build_wheel", "_install_wheel", "_assert_installed_product"):
        mocker.patch.object(nox_configuration, name)
    mocker.patch.object(nox_configuration, "_installed_executable", return_value=work / "proxy")

    def execute(*args, env=None, **_kwargs):
        command = (
            (sys.executable, "-m", "coverage", *args[1:])
            if args[0] == "coverage"
            else (sys.executable, *args[1:])
        )
        environment = {**os.environ, **(env or {})}
        return subprocess.run(
            command,
            cwd=tmp_path,
            env={key: value for key, value in environment.items() if value is not None},
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        ).stdout

    session.run.side_effect = execute
    nox_configuration.tests(session)

    assert (tmp_path / "attempt-count.txt").read_text() == "one\n"
    junit = ET.parse(output / f"{session.name}.xml")
    assert len(tuple(junit.iter("testcase"))) == 1
    measured = ET.parse(output / f"{session.name}-coverage.xml")
    assert any(item.get("filename") == "sample.py" for item in measured.iter("class"))
    runtime = json.loads((output / f"{session.name}-runtime.json").read_text())
    assert runtime["version"] == list(sys.version_info[:3])
    assert Path(runtime["executable"]).is_absolute()
    assert runtime["pytest"]["session"] == session.name
    assert runtime["pytest"]["root"] == str(tmp_path)
    assert runtime["pytest"]["configuration"] == str(configuration)
    assert (
        runtime["pytest"]["configuration_sha256"]
        == hashlib.sha256(configuration.read_bytes()).hexdigest()
    )
    assert runtime["pytest"]["exitstatus"] == 0
    behavior = [call for call in session.run.call_args_list if "pytest" in call.args]
    assert len(behavior) == 1
    assert behavior[0].kwargs["env"]["ETHOS_NATIVE_OUTPUT_DIR"] is None
    events_path = output / f"{session.name}-pytest.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert events[0]["$report_type"] == "SessionStart"
    assert events[-1] == {"exitstatus": 0, "$report_type": "SessionFinish"}
    assert [event["when"] for event in events if event["$report_type"] == "TestReport"] == [
        "setup",
        "call",
        "teardown",
    ]
    assert behavior[0].args.count("--report-log") == 1
    assert behavior[0].args[behavior[0].args.index("--report-log") + 1] == str(events_path)


@pytest.mark.repository_toolchain
def test_native_static_report_observes_one_original_ruff_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    configuration = tmp_path / "ruff.toml"
    configuration.write_text('[lint]\nselect = ["F"]\n')
    (tmp_path / "sample.py").write_text("def answer():\n    return 42\n")
    output = tmp_path / "reports"
    output.mkdir()
    runner = mocker.Mock(
        friendly_name="quality",
        func=SimpleNamespace(python=f"{sys.version_info.major}.{sys.version_info.minor}"),
        global_config=SimpleNamespace(install_only=False, error_on_external_run=True),
        venv=PassthroughEnv(bin_paths=[str(Path(sys.executable).parent)]),
    )
    session = Session(runner)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(nox_configuration.python_quality, "RUFF_CONFIG", configuration)
    nox_configuration.python_quality._static_report(
        session,
        ("ruff", "check", "--config", str(configuration), "--no-cache", "."),
        output=output,
        environment={"PYTHONDONTWRITEBYTECODE": "1"},
    )

    runtime = json.loads((output / "quality-ruff-runtime.json").read_text())
    checker = runtime["ruff"]
    assert checker["completed"] is True
    assert checker["launches"] == 1
    assert Path(checker["executable"]).parent == Path(sys.executable).parent
    assert checker["configuration"] == str(configuration)
    assert checker["configuration_sha256"] == hashlib.sha256(configuration.read_bytes()).hexdigest()
    assert checker["session"] == f"quality-{sys.version_info.major}.{sys.version_info.minor}"
    assert json.loads((output / "quality-ruff.json").read_text()) == []


@pytest.mark.parametrize(
    "session_name", ["tests", "quality", "release_asset", "release", "performance"]
)
@pytest.mark.parametrize(
    ("system", "machine", "release_platform"),
    [
        ("Darwin", "arm64", "macos-arm64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Windows", "AMD64", "windows-x86_64"),
    ],
)
def test_sessions_accept_built_artifacts_before_publication(
    session_name: str,
    system: str,
    machine: str,
    release_platform: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
    orchestration_session: Session,
) -> None:
    mocker.patch.object(
        nox_configuration,
        "platform",
        SimpleNamespace(
            system=lambda: system,
            machine=lambda: machine,
        ),
    )
    executable_name = nox_configuration.product_identity.executable_name(windows=os.name == "nt")
    packages = tmp_path / "site-packages"
    packages.mkdir()
    Path(orchestration_session.bin, executable_name).touch()

    def execute(args: tuple[str, ...], **_kwargs: object) -> str:
        if args[1:3] == ("build", "--wheel"):
            Path(args[args.index("--out-dir") + 1], "product.whl").touch()
        if args[:2] == ("python", "-c"):
            return str(packages) if "purelib" in args[2] else str(nox_configuration.RELEASE_PYTHON)
        if args[0] == "pyinstaller":
            bundle = Path(args[args.index("--distpath") + 1], args[args.index("--name") + 1])
            bundle.mkdir(parents=True)
            (bundle / executable_name).touch()
        return ""

    command = mocker.patch("nox.command.run", side_effect=execute)
    getattr(nox_configuration, session_name)(orchestration_session)

    calls = [call.args[0] for call in command.call_args_list]
    build = next(i for i, args in enumerate(calls) if args[1:3] == ("build", "--wheel"))
    install = next(
        i for i, args in enumerate(calls) if any(str(arg).endswith("product.whl") for arg in args)
    )
    admission = next(i for i, args in enumerate(calls) if args[:3] == ("python", "-I", "-c"))
    behavior = next(
        i
        for i, args in enumerate(calls)
        if "pytest" in args or "tools.performance.benchmark" in args
    )
    assert build < install < admission < behavior
    if session_name in {"release", "release_asset"}:
        (native_build,) = [args for args in calls if args[0] == "pyinstaller"]
        assert native_build[native_build.index("--additional-hooks-dir") + 1] == str(
            ROOT / "tools/release/hooks"
        )
        pack = next(
            i
            for i, args in enumerate(calls)
            if args[:4] == ("python", "-m", "tools.release.artifact", "pack")
        )
        assert behavior < pack
        tests = calls[behavior]
        assert ("tests/release/test_native_lifecycle.py" in tests) == (session_name == "release")
        assert "tests/cli/test_interface.py" in tests
        assert "tests/serving/handoff/test_subprocess.py" in tests
        assert calls[pack][-2:] == ("--output", orchestration_session.posargs[0])
        assert calls[pack][calls[pack].index("--platform") + 1] == release_platform
    elif session_name == "performance":
        performance = [
            args for args in calls if any(str(arg).startswith("tools.performance.") for arg in args)
        ]
        assert [args[2] for args in performance] == [
            "tools.performance.benchmark",
            "tools.performance.memory",
            "tools.performance.verify",
        ]
        assert "--track-memory" in performance[1]
        assert performance[-1][-4:] == (
            "--latency",
            str(tmp_path / "assets/latency.json"),
            "--memory",
            str(tmp_path / "assets/memory.json"),
        )
    else:
        assert calls[behavior][-6:] == (
            "-m",
            "not native_distribution and not repository_toolchain",
            "--rootdir",
            str(nox_configuration.python_quality.ROOT),
            "-c",
            str(nox_configuration.python_quality.PYTEST_CONFIG),
        )
    installs = [call for call in command.call_args_list if call.args[0][1:3] == ("pip", "install")]
    assert len(installs) == 2
    for call in installs:
        assert call.kwargs["env"]["VIRTUAL_ENV"] == orchestration_session.virtualenv.location
        assert call.kwargs["env"]["UV_PYTHON"] == orchestration_session.virtualenv.location


def test_full_acceptance_schedules_each_required_environment_once(
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    mocker.patch.object(nox_configuration, "PYTHONS", ("3.12", "3.13", "3.14"))
    nox_configuration.full(session)
    assert [call.args[0] for call in session.notify.call_args_list] == [
        "governance",
        "quality",
        "tests-3.13",
        "tests-3.14",
    ]


@pytest.mark.parametrize("groups", [(), ("quality", "release")])
def test_nox_tool_environment_contains_product_runtime_dependencies(
    groups: tuple[str, ...],
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock(python="3.12")
    session.create_tmp.return_value = str(tmp_path)

    nox_configuration._install_tools(session, *groups)

    command = session.run_install.call_args.args
    assert command == (
        "uv",
        "export",
        "--locked",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        str(tmp_path / "requirements.txt"),
        "--project",
        str(ROOT),
        *tuple(part for group in groups or ("quality",) for part in ("--group", group)),
    )
    session.install.assert_called_once_with(
        "--requirements",
        str(tmp_path / "requirements.txt"),
        "--strict",
        env={"PYTHONNOUSERSITE": "1", "UV_NO_PROGRESS": "1"},
    )


@pytest.mark.parametrize("session_name", ["release_asset", "release"])
def test_failed_native_acceptance_cannot_publish_an_asset(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    mocker.patch.object(
        nox_configuration,
        "_build_native_candidate",
        return_value=(tmp_path, tmp_path / "bundle", tmp_path / "proxy"),
    )
    failure = CommandFailed("native acceptance failed", return_code=1)
    session.run.side_effect = failure

    with pytest.raises(CommandFailed) as raised:
        getattr(nox_configuration, session_name)(session)

    assert raised.value is failure
    session.run.assert_called_once()
    assert session.run.call_args.args[:4] == ("python", "-m", "pytest", "-q")


def test_every_session_pytest_run_binds_the_explicit_configuration(
    nox_configuration: ModuleType,
) -> None:
    """Pytest finds no root configuration, so each Nox run names its owner and rootdir."""
    command = nox_configuration.PYTEST_COMMAND

    assert command[command.index("-c") + 1] == str(ROOT / ".config/checks/pytest/pytest.toml")
    assert command[command.index("--rootdir") + 1] == str(ROOT)
    assert not (ROOT / "pytest.ini").exists()


def test_fix_formats_python_then_text_through_their_owners(
    mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    session = mocker.Mock(posargs=[])

    nox_configuration.fix(session)

    assert [call.args for call in session.run.call_args_list] == [
        (
            "ruff",
            "format",
            "--config",
            str(nox_configuration.python_quality.RUFF_CONFIG),
            "--no-cache",
            ".",
        ),
        (sys.executable, "-m", "tools.quality.governance", "--fix"),
    ]


def test_governance_reuses_the_admitted_interpreter(
    mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    session = mocker.Mock(posargs=[])

    nox_configuration.governance(session)

    session.run.assert_called_once_with(
        sys.executable,
        "-m",
        "tools.quality.governance",
        external=True,
        env=nox_configuration._environment(),
    )


@pytest.mark.parametrize("session_name", ["quick", "quality"])
def test_static_checks_cover_the_same_source_and_configuration_scope(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    session.create_tmp.return_value = str(tmp_path)
    for name in (
        "_install_tools",
        "_build_wheel",
        "_install_wheel",
        "_assert_installed_product",
    ):
        mocker.patch.object(nox_configuration, name)
    mocker.patch.object(nox_configuration, "_installed_executable", return_value=tmp_path / "proxy")

    getattr(nox_configuration, session_name)(session)

    calls = [call.args for call in session.run.call_args_list]
    (lint,) = [call for call in calls if call[:2] == ("ruff", "check")]
    assert lint[lint.index("--config") + 1] == str(nox_configuration.python_quality.RUFF_CONFIG)
    (typing,) = [call for call in calls if call[:2] == ("ty", "check")]
    assert set(typing[-4:]) == {"src/openai_responses_proxy", "tools", "tests", "noxfile.py"}
    assert typing[typing.index("--python-platform") + 1] == "all"
    assert typing[typing.index("--python-version") + 1] == nox_configuration.MIN_PYTHON
    for module in ("tools.quality.responsibilities", "tools.quality.hard_coding"):
        (audit,) = (
            call for call in session.run.call_args_list if call.args[:3] == ("python", "-m", module)
        )
        assert audit.kwargs["silent"] is True


@pytest.mark.parametrize("session_name", ["quick", "quality"])
def test_static_failure_stops_acceptance_before_behavioral_gates(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    session.create_tmp.return_value = str(tmp_path)
    for name in (
        "_install_tools",
        "_build_wheel",
        "_install_wheel",
        "_assert_installed_product",
    ):
        mocker.patch.object(nox_configuration, name)
    mocker.patch.object(nox_configuration, "_installed_executable", return_value=tmp_path / "proxy")
    failure = CommandFailed("source validation failed", return_code=1)
    session.run.side_effect = failure

    with pytest.raises(CommandFailed) as raised:
        getattr(nox_configuration, session_name)(session)

    assert raised.value is failure
    session.run.assert_called_once()
    assert session.run.call_args.args[:2] == ("ruff", "check")
