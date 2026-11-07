"""Contracts for the single native artifact command tree."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.release.artifact.fixtures import native_inputs
from tools.release.artifact import __main__ as commands
from tools.release.artifact import assembly
from tools.release.artifact import format as assets

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


class ArtifactCommandContracts:
    """Keep artifact verbs explicit and bound to their semantic owners."""

    def test_verify_requires_a_valid_signature_not_just_a_signature_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = tmp_path / "release"
        assembly.assemble(native_inputs(tmp_path), output)
        (output / assets.SIGNATURE_NAME).write_bytes(b"not an OpenSSH signature")
        monkeypatch.setenv("RELEASE_ASSET_TRUST", "explicit invalid trust fixture")

        with pytest.raises(SystemExit) as result:
            commands.main(("verify", "--assets", str(output)))

        assert result.value.code == 1
        assert "verified release assets:" not in capsys.readouterr().out

    def test_assemble_writes_one_complete_artifact_inventory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        inputs = native_inputs(tmp_path)
        output = tmp_path / "release"
        arguments = tuple(argument for path in inputs for argument in ("--input", str(path)))
        commands.main(("assemble", *arguments, "--output", str(output)))
        unsigned = assets.release_asset_names(
            "1.2.3", assets.RELEASE_PLATFORMS, require_signature=False
        )
        assert {path.name for path in output.iterdir()} == unsigned
        assert capsys.readouterr().out.strip() == f"assembled release assets: {len(unsigned)} files"

    def test_asset_command_reads_the_signing_key_from_an_explicit_path(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        key = tmp_path / "signing"
        key.write_text("private key fixture", encoding="utf-8")
        assemble = mocker.patch.object(assembly, "assemble_sign_verify", return_value={})
        mocker.patch.dict(
            os.environ,
            {
                "RELEASE_ASSET_SIGNING_KEY_PATH": str(key),
                "RELEASE_ASSET_TRUST": "trust fixture",
            },
        )

        commands._assemble(inputs=(tmp_path,), output=tmp_path / "release", sign=True)

        assemble.assert_called_once_with(
            inputs=(tmp_path,),
            output=tmp_path / "release",
            key=key,
            trust="trust fixture",
        )
