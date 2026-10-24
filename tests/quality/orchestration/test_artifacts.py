"""Artifact admission and release input contracts."""

from __future__ import annotations

import os
import subprocess
import sysconfig
import venv
from concurrent.futures import Future
from contextlib import chdir
from pathlib import Path
from pathlib import PureWindowsPath
from types import ModuleType

import pytest
from pytest_mock import MockerFixture

from openai_responses_proxy.lifecycle import control as lifecycle_control
from tests.quality.fixtures import ROOT
from tests.quality.fixtures import git
from tests.release.compatibility.fixtures import _wait_for_upgrade_drain
from tests.release.fixtures import runtime_context_for


@pytest.mark.parametrize("defect", [None, "source-checkout", "changed", "extra", "missing"])
def test_installed_product_probe_distinguishes_environment_from_source_checkout(
    defect: str | None,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    site_packages = Path(
        sysconfig.get_path("purelib", vars={"base": str(environment), "platbase": str(environment)})
    )
    checkout = tmp_path / "checkout"
    source = checkout / "src" if defect == "source-checkout" else site_packages
    package = source / "openai_responses_proxy"
    providers = package / "providers"
    providers.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (providers / "__init__.py").write_text("", encoding="utf-8")
    (providers / "registry.py").write_text(
        "from pathlib import Path\n"
        "def default_manifest_path():\n"
        "    return Path(__file__).with_name('manifest.toml')\n",
        encoding="utf-8",
    )
    (providers / "manifest.toml").write_text("", encoding="utf-8")
    (package / "unused.py").write_text("VALUE = 42\n", encoding="utf-8")
    source_package = checkout / "src/openai_responses_proxy"
    for path in package.rglob("*"):
        if path.is_file():
            target = source_package / path.relative_to(package)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    git(checkout, "init", "-q", "--initial-branch=fixture-root")
    git(checkout, "add", "--", "src")
    if defect == "source-checkout":
        (site_packages / "source-checkout.pth").write_text(str(source) + "\n", encoding="utf-8")
    elif defect == "changed":
        (package / "unused.py").write_text("VALUE = 0\n", encoding="utf-8")
    elif defect == "extra":
        (package / "surplus.py").write_text("VALUE = 0\n", encoding="utf-8")
    elif defect == "missing":
        (package / "unused.py").unlink()
    work = checkout / "build"
    work.mkdir(parents=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    session = mocker.Mock()
    session.chdir.side_effect = chdir
    session.run.side_effect = lambda *args, **_kwargs: subprocess.run(
        [str(python), *args[1:]], check=True, capture_output=True, text=True
    )
    mocker.patch.object(nox_configuration, "ROOT", checkout)

    if defect is not None:
        with pytest.raises(subprocess.CalledProcessError):
            nox_configuration._assert_installed_product(session, work)
    else:
        nox_configuration._assert_installed_product(session, work)


@pytest.mark.parametrize("owner", ["_session_packages", "_installed_executable"])
def test_missing_build_output_cannot_establish_artifact_acceptance(
    owner: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock(bin=str(tmp_path))
    session.run.return_value = None
    session.error.side_effect = RuntimeError
    with pytest.raises(RuntimeError):
        getattr(nox_configuration, owner)(session)


def test_release_packaging_requires_a_supported_native_platform(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock(posargs=[])
    session.error.side_effect = RuntimeError
    mocker.patch.object(
        nox_configuration.construction.platform, "system", return_value="unsupported"
    )
    with pytest.raises(RuntimeError):
        nox_configuration.construction.pack(session, tmp_path / "bundle", tmp_path, environment={})
    session.run.assert_not_called()


def test_wheel_build_uses_an_external_ephemeral_cache(
    tmp_path: Path, mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    session = mocker.Mock()
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / "product.whl"
    observed_cache: Path | None = None

    def build(*args: str, **_kwargs: object) -> None:
        nonlocal observed_cache
        if "--cache-dir" in args:
            observed_cache = Path(args[args.index("--cache-dir") + 1])
            assert observed_cache.is_dir()
        wheel.touch()

    session.run_install.side_effect = build

    assert nox_configuration._build_wheel(session, tmp_path) == wheel
    assert observed_cache is not None
    assert not observed_cache.is_relative_to(ROOT)
    assert not observed_cache.exists()
    call = list(session.run_install.call_args.args)
    del call[call.index("--cache-dir") : call.index("--cache-dir") + 2]
    assert call == [
        "uv",
        "build",
        "--wheel",
        "--offline",
        "--no-build-isolation",
        "--python",
        "python",
        "--out-dir",
        str(wheelhouse),
        str(ROOT),
    ]
    assert session.run_install.call_args.kwargs == {"external": True}


def test_wheel_build_refuses_a_cache_inside_the_source_checkout(
    tmp_path: Path, mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    """A cache cannot contaminate the source inventory used to build its wheel."""
    session = mocker.Mock()
    cache = mocker.Mock()
    cache.__enter__ = mocker.Mock(return_value=str(ROOT / "cache"))
    cache.__exit__ = mocker.Mock(return_value=False)
    mocker.patch.object(nox_configuration, "TemporaryDirectory", return_value=cache)
    with pytest.raises(RuntimeError, match="outside the source checkout"):
        nox_configuration._build_wheel(session, tmp_path)
    session.run_install.assert_not_called()


@pytest.mark.parametrize("wheel_count", [0, 2])
def test_wheel_build_requires_one_unambiguous_artifact(
    wheel_count: int,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    session.error.side_effect = RuntimeError

    def build(*_args: str, **_kwargs: object) -> None:
        for index in range(wheel_count):
            (tmp_path / "wheelhouse" / f"product-{index}.whl").touch()

    session.run_install.side_effect = build
    with pytest.raises(RuntimeError):
        nox_configuration._build_wheel(session, tmp_path)
    session.error.assert_called_once_with(f"expected one wheel, found {wheel_count}")


@pytest.mark.parametrize("missing_input", ["asset", "trust"])
def test_release_compatibility_requires_real_predecessor_inputs_before_build(
    missing_input: str | None,
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock()
    asset, trust = tmp_path / "previous.zip", tmp_path / "trust.json"
    for role, path, suffix in (
        ("asset", asset, "PREVIOUS_RELEASE_ASSET"),
        ("trust", trust, "PREVIOUS_RELEASE_TRUST_ANCHOR"),
    ):
        name = nox_configuration.product_identity.environment_name(suffix)
        if role == missing_input:
            monkeypatch.delenv(name, raising=False)
        else:
            path.touch()
            monkeypatch.setenv(name, str(path))
    build = mocker.patch.object(
        nox_configuration,
        "_build_native_candidate",
        return_value=(tmp_path, tmp_path / "bundle", tmp_path / "proxy"),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        nox_configuration.release_compatibility(session)
    build.assert_not_called()
    session.run.assert_not_called()


def test_published_release_compatibility_uses_supplied_release_bytes(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    """Exercise the downloaded release instead of rebuilding its candidate."""
    current = tmp_path / "current.tar.gz"
    previous = tmp_path / "previous.tar.gz"
    trust = tmp_path / "allowed-signers"
    for suffix, path in (
        ("CURRENT_RELEASE_ASSET", current),
        ("PREVIOUS_RELEASE_ASSET", previous),
        ("PREVIOUS_RELEASE_TRUST_ANCHOR", trust),
        ("CURRENT_RELEASE_TRUST_ANCHOR", trust),
    ):
        path.touch()
        monkeypatch.setenv(nox_configuration.product_identity.environment_name(suffix), str(path))
    executable = tmp_path / "bundle" / "openai-responses-proxy"
    materialize = mocker.patch.object(
        nox_configuration.verification,
        "materialize_published_bundle",
        return_value=(executable.parent, executable),
    )
    wheel = tmp_path / "product.whl"
    build = mocker.patch.object(nox_configuration, "_build_wheel", return_value=wheel)
    install = mocker.patch.object(nox_configuration, "_install_wheel")
    assert_installed = mocker.patch.object(nox_configuration, "_assert_installed_product")
    native_build = mocker.patch.object(nox_configuration, "_build_native_candidate")
    test_root = tmp_path / "proxy-test"
    session = mocker.Mock(posargs=[f"--basetemp={test_root}"])
    session.create_tmp.return_value = str(tmp_path / "work")

    nox_configuration.published_release_compatibility(session)

    session.run.assert_any_call(
        "python",
        "-c",
        "import platform; print(platform.python_version())",
        env=nox_configuration._environment(),
        silent=True,
    )
    build.assert_called_once_with(session, tmp_path / "work")
    install.assert_called_once_with(session, wheel)
    assert_installed.assert_called_once_with(session, tmp_path / "work")
    native_build.assert_not_called()
    materialize.assert_called_once_with(
        session,
        current,
        trust,
        tmp_path / "work",
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
    )
    proof = session.run.call_args_list[-1]
    assert proof.args == (
        *nox_configuration.PYTEST_COMMAND,
        "-rs",
        "tests/cli/test_interface.py",
        "tests/serving/handoff/subprocess",
        "tests/relay/native/test_recovery.py",
        "tests/release/lifecycle",
        "tests/release/compatibility",
        f"--basetemp={test_root}",
    )
    environment = proof.kwargs["env"]
    assert environment[nox_configuration.product_identity.environment_name("EXECUTABLE")] == str(
        executable
    )
    assert environment[
        nox_configuration.product_identity.environment_name("NATIVE_EXECUTABLE")
    ] == str(executable)
    assert environment[nox_configuration.product_identity.environment_name("NATIVE_BUNDLE")] == str(
        executable.parent
    )
    assert environment[
        nox_configuration.product_identity.environment_name("PREVIOUS_RELEASE_ASSET")
    ] == str(previous)
    assert environment[
        nox_configuration.product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR")
    ] == str(trust)


def test_release_runtime_uses_the_session_interpreter(
    mocker: MockerFixture, nox_configuration: ModuleType
) -> None:
    """Compare the release session interpreter, not the Nox launcher."""
    session = mocker.Mock()
    session.run.return_value = "3.14.7\n"

    nox_configuration._assert_release_runtime(session)

    session.run.assert_called_once_with(
        "python",
        "-c",
        "import platform; print(platform.python_version())",
        env=nox_configuration._environment(),
        silent=True,
    )
    session.run.return_value = "3.14.6\n"
    session.error.side_effect = RuntimeError
    with pytest.raises(RuntimeError):
        nox_configuration._assert_release_runtime(session)


def test_upgrade_drain_releases_held_requests(tmp_path: Path, *, mocker) -> None:
    """Release held requests once native replacement closes admission."""
    ctx = runtime_context_for(
        tmp_path / "home",
        tmp_path / "payload",
        tmp_path / "state",
        43210,
    )
    future: Future[dict[str, object]] = Future()
    mocker.patch.object(
        lifecycle_control,
        "read_runtime",
        return_value={
            "pid": 1234,
            "release": "3.1.2",
            "accepting": False,
            "draining": True,
        },
    )

    assert _wait_for_upgrade_drain(
        future,
        ctx,
        predecessor_pid=1234,
        predecessor_release="3.1.2",
        timeout_seconds=0.01,
    )


def test_upgrade_drain_waits_through_materialization(tmp_path: Path, *, mocker) -> None:
    """A materialized candidate is not yet at the native drain boundary."""
    ctx = runtime_context_for(
        tmp_path / "home",
        tmp_path / "payload",
        tmp_path / "state",
        43210,
    )
    future: Future[dict[str, object]] = Future()
    mocker.patch.object(
        lifecycle_control,
        "read_runtime",
        side_effect=[
            {
                "pid": 1234,
                "release": "3.1.2",
                "accepting": True,
                "draining": False,
            },
            {
                "pid": 1234,
                "release": "3.1.2",
                "accepting": False,
                "draining": True,
            },
        ],
    )
    mocker.patch(
        "tests.serving.handoff.fixtures.time.monotonic",
        side_effect=[0.0, 0.0, 0.2],
    )
    mocker.patch("tests.serving.handoff.fixtures.time.sleep")

    assert _wait_for_upgrade_drain(
        future,
        ctx,
        predecessor_pid=1234,
        predecessor_release="3.1.2",
        timeout_seconds=0.2,
    )


def test_runtime_context_uses_the_native_command_projection(tmp_path: Path, *, mocker) -> None:
    """Build release-test paths through the same owner as production."""
    mocker.patch("tests.release.fixtures.platform.system", return_value="Windows")
    home = tmp_path / "home"
    install = tmp_path / "payload"
    ctx = runtime_context_for(home, install, tmp_path / "state", 43210)

    assert PureWindowsPath(ctx.executable) == PureWindowsPath(
        install / "bin" / "openai-responses-proxy.exe"
    )
    assert PureWindowsPath(ctx.command) == PureWindowsPath(
        home / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "openai-responses-proxy.cmd"
    )


@pytest.mark.parametrize("invalid", [None, "python:3.14.7-bookworm@sha256:a"])
def test_release_runtime_refuses_invalid_projection_before_build(
    tmp_path, mocker, nox_configuration, invalid
):
    release = tmp_path / ".config/mise/python-release"
    release.parent.mkdir(parents=True)
    release.write_text("3.14.7\n")
    if invalid is not None:
        image = tmp_path / ".config/ci/linux-release-image"
        image.parent.mkdir(parents=True)
        image.write_text(invalid + "\n")
    mocker.patch.object(nox_configuration, "ROOT", tmp_path)
    session = mocker.Mock()
    session.error.side_effect = RuntimeError
    with pytest.raises(RuntimeError):
        nox_configuration._assert_release_runtime(session)
    session.run.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_predecessor_acceptance_consumes_the_built_candidate_without_rebuilding(
    missing: bool,
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    nox_configuration: ModuleType,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in ("PREVIOUS_RELEASE_ASSET", "PREVIOUS_RELEASE_TRUST_ANCHOR"):
        path = tmp_path / suffix
        path.touch()
        monkeypatch.setenv(nox_configuration.product_identity.environment_name(suffix), str(path))
    variable = nox_configuration.product_identity.environment_name("CURRENT_RELEASE_ASSETS")
    if missing:
        monkeypatch.delenv(variable, raising=False)
    else:
        monkeypatch.setenv(variable, str(assets))
    work = tmp_path / "work"
    work.mkdir()
    mocker.patch.object(nox_configuration, "ROOT", tmp_path)
    build = mocker.patch.object(nox_configuration, "_build_native_candidate")
    prepare = mocker.patch.object(nox_configuration, "_prepare_product_session", return_value=work)
    mocker.patch.object(nox_configuration, "_assert_release_runtime")
    session = mocker.Mock(posargs=[])
    executable = (
        work
        / "candidate/bin"
        / nox_configuration.product_identity.executable_name(windows=os.name == "nt")
    )

    def execute(*arguments: str, **_kwargs: object) -> None:
        if "materialize" in arguments:
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"existing native candidate")

    session.run.side_effect = execute
    if missing:
        with pytest.raises(RuntimeError, match=r"candidate.*unavailable"):
            nox_configuration.release_compatibility(session)
        prepare.assert_not_called()
        session.run.assert_not_called()
    else:
        nox_configuration.release_compatibility(session)
        materialize = session.run.call_args_list[0].args
        assert materialize == (
            "python",
            "-m",
            "tools.release.artifact",
            "materialize",
            "--source",
            str(assets),
            "--output",
            str(work / "candidate"),
        )
        acceptance = session.run.call_args_list[-1]
        assert acceptance.args[:-1] == (
            *nox_configuration.PYTEST_COMMAND,
            "tests/release/compatibility",
        )
        assert acceptance.kwargs["env"][
            nox_configuration.product_identity.environment_name("NATIVE_EXECUTABLE")
        ] == str(executable)
        assert acceptance.kwargs["env"][
            nox_configuration.product_identity.environment_name("NATIVE_BUNDLE")
        ] == str(executable.parent)
    build.assert_not_called()


def test_native_freeze_requires_the_produced_executable(tmp_path, mocker, nox_configuration):
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    with pytest.raises(RuntimeError, match="native executable was not produced"):
        nox_configuration.construction.freeze(session, tmp_path, root=tmp_path, environment={})
