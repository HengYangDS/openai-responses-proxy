"""Contracts for deterministic, checksum-bound release assets."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from typing import Literal

import pytest

from tests.release.artifact.fixtures import native_inputs
from tools.release.artifact import __main__ as commands
from tools.release.artifact import assembly
from tools.release.artifact import format as assets

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


class AssemblyContracts:
    """Verify the assembly owner of native release artifacts."""

    def test_native_platform_outputs_assemble_into_one_release(self, tmp_path: Path) -> None:
        inputs = native_inputs(tmp_path)
        version = "1.2.3"
        output = tmp_path / "release"
        release = assembly.assemble(tuple(inputs), output)
        assert set(release) == assets.release_asset_names(
            version, assets.RELEASE_PLATFORMS, require_signature=False
        )
        assets.release_digests(release, version, assets.RELEASE_PLATFORMS, require_signature=False)

    def test_native_outputs_are_assembled_signed_and_verified_by_one_owner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        inputs = native_inputs(tmp_path)
        key = tmp_path / "signing"
        subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
        public = key.with_suffix(".pub").read_text().strip()
        trust = (
            f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
        )

        digests = assembly.assemble_sign_verify(
            inputs=tuple(inputs), output=tmp_path / "release", key=key, trust=trust
        )

        assert set(digests) == assets.release_asset_names("1.2.3", assets.RELEASE_PLATFORMS)
        assert key.is_file()
        assert assembly.verify(tmp_path / "release", trust=trust) == digests
        monkeypatch.setenv("RELEASE_ASSET_TRUST", trust)
        commands.main(("verify", "--assets", str(tmp_path / "release")))
        assert capsys.readouterr().out.strip() == f"verified release assets: {len(digests)} files"


@pytest.mark.parametrize(
    "defect", ["occupied-output", "duplicate", "unknown", "missing", "no-inputs"]
)
def test_assembly_rejects_ambiguous_or_incomplete_inventory(
    tmp_path: Path,
    defect: Literal["occupied-output", "duplicate", "unknown", "missing", "no-inputs"],
) -> None:
    inputs = native_inputs(tmp_path)
    output = tmp_path / "release"
    if defect == "occupied-output":
        output.mkdir()
        (output / "preserved").write_bytes(b"owned")
    elif defect == "duplicate":
        inputs = (*inputs, inputs[0])
    elif defect == "unknown":
        (inputs[0] / "unknown").write_bytes(b"x")
    elif defect == "missing":
        inputs = inputs[:1]
    else:
        inputs = ()
    with pytest.raises(assets.AssetError):
        assembly.assemble(inputs, output)
    if defect == "occupied-output":
        assert (output / "preserved").read_bytes() == b"owned"
    else:
        assert not output.exists()


def test_assembly_preserves_missing_signer_and_signature_failures(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    output = tmp_path / "release"
    with pytest.raises(assets.AssetError, match="signing inputs"):
        assembly.assemble_sign_verify(
            inputs=(), output=output, key=tmp_path / "absent", trust="trust"
        )
    assert not output.exists()
    key = tmp_path / "key"
    key.touch()
    inputs = native_inputs(tmp_path)
    failure = assembly.signing.SignatureError("invalid")
    mocker.patch.object(assembly.signing, "sign_and_verify", side_effect=failure)
    with pytest.raises(assets.AssetError, match="signature is invalid"):
        assembly.assemble_sign_verify(inputs=inputs, output=output, key=key, trust="trust")
    mocker.patch.object(assembly.signing, "verify", side_effect=failure)
    with pytest.raises(assets.AssetError, match="signature is invalid"):
        assembly.verify(output, trust="trust")
