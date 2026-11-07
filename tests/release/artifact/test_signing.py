"""Shared release-asset signature contracts."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from tools.release.artifact import signing

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def test_sign_and_verify_uses_one_external_trust_boundary(tmp_path: Path) -> None:
    key, assets = tmp_path / "signing", tmp_path / "assets"
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'

    signing.sign_and_verify(assets=assets, key=key, trust=trust)

    assert (assets / "SHA256SUMS.sig").is_file()
    assert not (assets / ".release-asset-trust").exists()


def test_sign_and_verify_preserves_complete_provider_key_path(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    key, assets = tmp_path / "signing", tmp_path / "assets"
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    temporary = mocker.spy(signing.tempfile, "TemporaryDirectory")

    signing.sign_and_verify(assets=assets, key=key, trust=trust)

    assert temporary.call_count == 1  # Verification trust anchor only.


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-variable newline repair")
def test_sign_and_verify_accepts_posix_file_variable_key_without_final_newline(
    tmp_path: Path,
) -> None:
    key, assets = tmp_path / "signing", tmp_path / "assets"
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    key.write_bytes(key.read_bytes().rstrip(b"\n"))

    signing.sign_and_verify(assets=assets, key=key, trust=trust)

    assert (assets / "SHA256SUMS.sig").is_file()


def test_sign_and_verify_does_not_rewrite_incomplete_windows_key(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    key, assets = tmp_path / "signing", tmp_path / "assets"
    key.write_bytes(b"incomplete-private-key")
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    run = mocker.patch.object(
        signing.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(255, ("ssh-keygen", "-Y", "sign")),
    )
    temporary = mocker.patch.object(
        signing.tempfile,
        "TemporaryDirectory",
        side_effect=AssertionError("Windows key ACL must remain provider-owned"),
    )
    mocker.patch.object(signing.shutil, "which", return_value="ssh-keygen")
    mocker.patch.object(signing.os, "name", "nt")
    mocker.patch.object(signing, "verify")

    with pytest.raises(signing.SignatureError, match="OpenSSH rejected"):
        signing.sign_and_verify(assets=assets, key=key, trust="release trust")

    assert run.call_args.args[0][5] == str(key)
    temporary.assert_not_called()


def test_sign_and_verify_rejects_missing_key_and_wrong_trust(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    with pytest.raises(signing.SignatureError):
        signing.sign_and_verify(assets=assets, key=tmp_path / "missing", trust="")


def test_signing_failure_reports_the_openssh_reason_without_traceback(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Keep CI failure output concise while preserving the actionable cause."""
    key, assets = tmp_path / "signing", tmp_path / "assets"
    key.write_text("incomplete private key", encoding="utf-8")
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    error = subprocess.CalledProcessError(
        255,
        ("ssh-keygen", "-Y", "sign"),
        stderr=b"Load key signing: error in libcrypto\n",
    )
    mocker.patch.object(subprocess, "run", side_effect=error)
    mocker.patch.object(signing.shutil, "which", return_value="/usr/bin/ssh-keygen")

    with pytest.raises(signing.SignatureError, match="OpenSSH rejected the release signing key"):
        signing.sign_and_verify(assets=assets, key=key, trust="release trust")


def test_verify_uses_external_trust_without_mutating_assets(tmp_path: Path) -> None:
    key, assets = tmp_path / "signing", tmp_path / "assets"
    assets.mkdir()
    (assets / "SHA256SUMS").write_text("abc\n", encoding="ascii")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    signing.sign_and_verify(assets=assets, key=key, trust=trust)
    before = {path.name: path.read_bytes() for path in assets.iterdir()}

    signing.verify(assets=assets, trust=trust)

    assert {path.name: path.read_bytes() for path in assets.iterdir()} == before
