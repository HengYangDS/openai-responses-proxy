"""Artifact admission and release input contracts."""

from __future__ import annotations

import os
import subprocess
import sysconfig
import venv
from contextlib import chdir
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT
from tests.quality.fixtures import git


def _select_released_host(
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> str:
    """Bind artifact-unit tests to the released host for this OS family."""
    if nox_configuration.os.name == "nt":
        system, machine = "Windows", "AMD64"
    elif nox_configuration.platform.system() == "Darwin":
        system, machine = "Darwin", "arm64"
    else:
        system, machine = "Linux", "x86_64"
    mocker.patch.object(nox_configuration.platform, "system", return_value=system)
    mocker.patch.object(nox_configuration.platform, "machine", return_value=machine)
    platform_id = nox_configuration.product_identity.native_release_platform(system, machine)
    assert isinstance(platform_id, str)
    return platform_id


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

    if defect:
        with pytest.raises(subprocess.CalledProcessError):
            nox_configuration._assert_installed_product(session, work)
    else:
        nox_configuration._assert_installed_product(session, work)


@pytest.mark.parametrize(
    "owner", ["_session_packages", "_installed_executable", "_build_executable"]
)
def test_missing_build_output_cannot_establish_artifact_acceptance(
    owner: str,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock(bin=str(tmp_path))
    session.run.return_value = None
    session.error.side_effect = RuntimeError
    arguments = (session, tmp_path) if owner == "_build_executable" else (session,)
    with pytest.raises(RuntimeError):
        getattr(nox_configuration, owner)(*arguments)


def test_release_packaging_requires_a_supported_native_platform(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    session = mocker.Mock(posargs=[])
    session.error.side_effect = RuntimeError
    mocker.patch.object(nox_configuration.platform, "system", return_value="unsupported")
    with pytest.raises(RuntimeError):
        nox_configuration._package_release_asset(session, tmp_path / "bundle", tmp_path)
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


@pytest.mark.parametrize("missing_input", [None, "asset", "trust"])
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
    if missing_input:
        with pytest.raises(RuntimeError, match="unavailable"):
            nox_configuration.release_compatibility(session)
        build.assert_not_called()
        session.run.assert_not_called()
    else:
        nox_configuration.release_compatibility(session)
        session.run.assert_called_once()
        assert session.run.call_args.args == (
            *nox_configuration.PYTEST_COMMAND,
            "tests/release/compatibility",
        )
        environment = session.run.call_args.kwargs["env"]
        assert environment[
            nox_configuration.product_identity.environment_name("PREVIOUS_RELEASE_ASSET")
        ] == str(asset)
        assert environment[
            nox_configuration.product_identity.environment_name("PREVIOUS_RELEASE_TRUST_ANCHOR")
        ] == str(trust)


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
        nox_configuration,
        "_materialize_published_bundle",
        return_value=(executable.parent, executable),
    )
    wheel = tmp_path / "product.whl"
    build = mocker.patch.object(nox_configuration, "_build_wheel", return_value=wheel)
    install = mocker.patch.object(nox_configuration, "_install_wheel")
    assert_installed = mocker.patch.object(nox_configuration, "_assert_installed_product")
    native_build = mocker.patch.object(nox_configuration, "_build_native_candidate")
    session = mocker.Mock(posargs=["--basetemp=/short/proxy-test"])
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
    materialize.assert_called_once_with(session, current, trust, tmp_path / "work")
    proof = session.run.call_args_list[-1]
    assert proof.args == (
        *nox_configuration.PYTEST_COMMAND,
        "tests/cli/test_interface.py",
        "tests/serving/handoff/subprocess",
        "tests/relay/native/test_recovery.py",
        "tests/release/lifecycle",
        "tests/release/compatibility",
        "--basetemp=/short/proxy-test",
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


def test_published_bundle_must_match_the_checked_out_release_version(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """Reject a signed asset attached to the wrong release identity."""
    released = mocker.Mock()
    released.version = "0.0.0"
    released.receipt = {"platform": _select_released_host(mocker, nox_configuration)}
    mocker.patch.object(nox_configuration.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))

    with pytest.raises(RuntimeError, match="version does not match"):
        nox_configuration._materialize_published_bundle(
            session,
            tmp_path / "current.tar.gz",
            tmp_path / "allowed-signers",
            tmp_path / "work",
        )

    released.peek_blobs.assert_not_called()


def test_published_bundle_must_match_the_native_platform(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """Reject a signed asset for a different native platform."""
    _select_released_host(mocker, nox_configuration)
    released = mocker.Mock(
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
        receipt={"platform": "unsupported-platform"},
    )
    mocker.patch.object(nox_configuration.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))

    with pytest.raises(RuntimeError, match="native host"):
        nox_configuration._materialize_published_bundle(
            session,
            tmp_path / "current.tar.gz",
            tmp_path / "allowed-signers",
            tmp_path / "work",
        )

    released.peek_blobs.assert_not_called()


@pytest.mark.parametrize("include_executable", [False, True])
def test_published_bundle_materializes_only_the_admitted_inventory(
    include_executable: bool,
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """Preserve admitted bytes and modes while requiring the native executable."""
    platform_id = _select_released_host(mocker, nox_configuration)
    executable_name = nox_configuration.product_identity.executable_name(
        windows=nox_configuration.os.name == "nt"
    )
    blobs = [SimpleNamespace(path="providers.toml", content=b"version = 1\n", mode="100644")]
    if include_executable:
        blobs.append(
            SimpleNamespace(path=f"bin/{executable_name}", content=b"native", mode="100755")
        )
    released = mocker.Mock(
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
        receipt={"platform": platform_id},
    )
    released.peek_blobs.return_value = tuple(blobs)
    mocker.patch.object(nox_configuration.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    work = tmp_path / "work"
    work.mkdir()

    if not include_executable:
        with pytest.raises(RuntimeError, match="executable was not materialized"):
            nox_configuration._materialize_published_bundle(
                session,
                tmp_path / "current.tar.gz",
                tmp_path / "allowed-signers",
                work,
            )
        return

    bundle, executable = nox_configuration._materialize_published_bundle(
        session,
        tmp_path / "current.tar.gz",
        tmp_path / "allowed-signers",
        work,
    )

    assert bundle == work / "published-release" / "bin"
    assert executable == bundle / executable_name
    assert executable.read_bytes() == b"native"
    providers = work / "published-release" / "providers.toml"
    assert providers.read_bytes() == b"version = 1\n"
    if nox_configuration.os.name != "nt":
        assert executable.stat().st_mode & 0o777 == 0o755
        assert providers.stat().st_mode & 0o777 == 0o644


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
