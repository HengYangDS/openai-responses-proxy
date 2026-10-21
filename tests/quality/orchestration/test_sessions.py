"""Verification session ordering and failure propagation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from nox.command import CommandFailed
from nox.sessions import Session
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


@pytest.mark.parametrize(
    "session_name", ["quick", "tests", "quality", "release_asset", "release", "performance"]
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
        assert ("tests/release/lifecycle" in tests) == (session_name == "release")
        assert "tests/cli/test_interface.py" in tests
        assert "tests/serving/handoff/subprocess" in tests
        assert "tests/relay/native/test_recovery.py" in tests
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
    elif session_name == "quick":
        assert calls[behavior][-3:] == (
            "tests/quality/test_policy.py",
            "tests/quality/repository",
            "tests/quality/orchestration/test_governance.py",
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
    environment = {**nox_configuration._environment(), "UV_NO_PROGRESS": "1"}
    assert session.run_install.call_args.kwargs["env"] == environment
    session.install.assert_called_once_with(
        "--requirements",
        str(tmp_path / "requirements.txt"),
        "--strict",
        env=environment,
    )


@pytest.mark.parametrize("session_name", ["release_asset", "release"])
def test_failed_native_acceptance_cannot_publish_an_asset(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    session.posargs = []
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


@pytest.mark.parametrize("session_name", ["release_asset", "release"])
def test_native_builder_preserves_the_callers_explicit_test_root(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """A native caller can avoid deep frozen-library paths without losing custody."""
    output = tmp_path / "assets"
    selected = tmp_path / "selected"
    session = mocker.Mock()
    session.posargs = [str(output), f"--basetemp={selected}"]
    mocker.patch.object(
        nox_configuration,
        "_build_native_candidate",
        return_value=(tmp_path, tmp_path / "bundle", tmp_path / "proxy"),
    )
    mocker.patch.object(nox_configuration, "ROOT", tmp_path)
    observed = []

    def populate(*arguments: str, **_kwargs: object) -> None:
        if "pytest" not in arguments:
            return
        assert str(output) not in arguments
        root = Path(
            next(item.split("=", 1)[1] for item in arguments if item.startswith("--basetemp="))
        )
        observed.append(root)
        root.mkdir(parents=True)
        (root / "owned.txt").write_bytes(b"native test scratch")

    session.run.side_effect = populate
    getattr(nox_configuration, session_name)(session)

    assert observed == [selected]
    assert not selected.exists()
    assert session.run.call_args.args[-2:] == ("--output", str(output))
    assert list((tmp_path / "build" / "native-verification").iterdir()) == []


@pytest.mark.parametrize("session_name", ["release_asset", "release"])
@pytest.mark.parametrize(
    "arguments", [["--collect-only"], ["-k", "test_subset"], ["--basetemp=one", "--basetemp=two"]]
)
def test_native_builders_refuse_arguments_that_replace_complete_acceptance(
    session_name: str,
    arguments: list[str],
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    session.posargs = [str(tmp_path / "assets"), *arguments]
    session.error.side_effect = RuntimeError("native acceptance arguments refused")
    build = mocker.patch.object(
        nox_configuration,
        "_build_native_candidate",
        return_value=(tmp_path, tmp_path / "bundle", tmp_path / "proxy"),
    )
    accept = mocker.patch.object(nox_configuration.verification, "accept")
    package = mocker.patch.object(nox_configuration, "_package_release_asset")

    with pytest.raises(RuntimeError, match="native acceptance arguments refused"):
        getattr(nox_configuration, session_name)(session)

    build.assert_not_called()
    accept.assert_not_called()
    package.assert_not_called()


@pytest.mark.parametrize("session_name", ["release", "release_compatibility", "native_lifecycle"])
def test_native_acceptance_claims_a_private_test_root_before_pytest(
    session_name: str,
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    """An aborted pytest child cannot leave its caller-owned temporary root."""
    session = mocker.Mock()
    session.posargs = []
    mocker.patch.object(nox_configuration, "ROOT", tmp_path)
    mocker.patch.object(
        nox_configuration,
        "_build_native_candidate",
        return_value=(tmp_path, tmp_path / "bundle", tmp_path / "proxy"),
    )
    executable = tmp_path / "proxy"
    executable.touch()
    monkeypatch.setenv(
        nox_configuration.product_identity.environment_name("NATIVE_EXECUTABLE"), str(executable)
    )
    for suffix in ("PREVIOUS_RELEASE_ASSET", "PREVIOUS_RELEASE_TRUST_ANCHOR"):
        path = tmp_path / suffix
        path.touch()
        monkeypatch.setenv(nox_configuration.product_identity.environment_name(suffix), str(path))
    owned: list[Path] = []
    failure = CommandFailed("test process was terminated", return_code=-9)

    def abort_child(*arguments: str, **_kwargs: object) -> None:
        if "pytest" not in arguments:
            return
        basetemp = next(
            (
                argument.split("=", 1)[1]
                for argument in arguments
                if argument.startswith("--basetemp=")
            ),
            None,
        )
        assert basetemp is not None, "native caller must claim the test root before launch"
        root = Path(basetemp)
        assert root.is_relative_to(tmp_path / "build" / "native-verification"), (
            "cleanup custody must survive recreation of the Nox environment"
        )
        root.mkdir(parents=True, exist_ok=True)
        (root / "interrupted-test.txt").write_text("owned test scratch", encoding="utf-8")
        owned.append(root)
        raise failure

    session.run.side_effect = abort_child
    with pytest.raises(CommandFailed) as raised:
        getattr(nox_configuration, session_name)(session)

    assert raised.value is failure
    assert len(owned) == 1
    assert not owned[0].exists()


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


def test_nox_environment_selects_the_native_uv_configuration(
    monkeypatch: pytest.MonkeyPatch, nox_configuration: ModuleType
) -> None:
    monkeypatch.setenv("UV_CONFIG_FILE", "/unowned/uv.toml")
    monkeypatch.setenv("UV_NO_CONFIG", "1")

    environment = nox_configuration._environment()

    assert environment["UV_CONFIG_FILE"] == str(ROOT / ".config/mise/uv.toml")
    assert environment["UV_NO_CONFIG"] == "false"
    assert "UV_LINK_MODE" not in environment


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
