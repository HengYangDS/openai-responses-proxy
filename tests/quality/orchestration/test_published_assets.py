"""Admit and materialize only the selected published native release inventory."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT


def _select_released_host(
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> str:
    """Bind artifact-unit tests to the released host for this OS family."""
    if nox_configuration.os.name == "nt":
        system, machine = "Windows", "AMD64"
    elif nox_configuration.verification.platform.system() == "Darwin":
        system, machine = "Darwin", "arm64"
    else:
        system, machine = "Linux", "x86_64"
    mocker.patch.object(nox_configuration.verification.platform, "system", return_value=system)
    mocker.patch.object(nox_configuration.verification.platform, "machine", return_value=machine)
    platform_id = nox_configuration.product_identity.native_release_platform(system, machine)
    assert isinstance(platform_id, str)
    return platform_id


def test_published_bundle_must_match_the_checked_out_release_version(
    tmp_path: Path,
    mocker: MockerFixture,
    nox_configuration: ModuleType,
) -> None:
    """Reject a signed asset attached to the wrong release identity."""
    released = mocker.Mock()
    released.version = "0.0.0"
    released.receipt = {"platform": _select_released_host(mocker, nox_configuration)}
    mocker.patch.object(nox_configuration.verification.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))

    with pytest.raises(RuntimeError, match="version does not match"):
        nox_configuration.verification.materialize_published_bundle(
            session,
            tmp_path / "current.tar.gz",
            tmp_path / "allowed-signers",
            tmp_path / "work",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
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
    mocker.patch.object(nox_configuration.verification.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))

    with pytest.raises(RuntimeError, match="native host"):
        nox_configuration.verification.materialize_published_bundle(
            session,
            tmp_path / "current.tar.gz",
            tmp_path / "allowed-signers",
            tmp_path / "work",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
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
    mocker.patch.object(nox_configuration.verification.artifact, "admit", return_value=released)
    session = mocker.Mock()
    session.error.side_effect = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    work = tmp_path / "work"
    work.mkdir()

    if not include_executable:
        with pytest.raises(RuntimeError, match="executable was not materialized"):
            nox_configuration.verification.materialize_published_bundle(
                session,
                tmp_path / "current.tar.gz",
                tmp_path / "allowed-signers",
                work,
                version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
            )
        return

    bundle, executable = nox_configuration.verification.materialize_published_bundle(
        session,
        tmp_path / "current.tar.gz",
        tmp_path / "allowed-signers",
        work,
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
    )

    assert bundle == work / "published-release" / "bin"
    assert executable == bundle / executable_name
    assert executable.read_bytes() == b"native"
    providers = work / "published-release" / "providers.toml"
    assert providers.read_bytes() == b"version = 1\n"
    if nox_configuration.os.name != "nt":
        assert executable.stat().st_mode & 0o777 == 0o755
        assert providers.stat().st_mode & 0o777 == 0o644
