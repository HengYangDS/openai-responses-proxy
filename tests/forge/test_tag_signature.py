"""Local product-tag verification against one explicit external anchor."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.forge import tag_signature


@pytest.mark.parametrize("tag", ["latest", "1.2.3", "v1.2", "v1.2.3."])
def test_verify_rejects_any_non_product_tag_name(tag: str, tmp_path: Path) -> None:
    """Refuse a tag name outside the strict product grammar before any Git call."""
    anchor = tmp_path / "allowed-signers"
    anchor.write_text("external trust\n", encoding="utf-8")
    with pytest.raises(tag_signature.TagSignatureError, match="must be v<semver>"):
        tag_signature.verify(tmp_path, tag, anchor)


def test_verify_requires_one_external_regular_anchor(tmp_path: Path) -> None:
    """Treat a missing, wrong-kind, or symlinked anchor as unavailable trust."""
    missing = tmp_path / "absent"
    with pytest.raises(tag_signature.TagSignatureError, match="anchor is unavailable"):
        tag_signature.verify(tmp_path, "v1.2.3", missing)

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(tag_signature.TagSignatureError, match="anchor is unavailable"):
        tag_signature.verify(tmp_path, "v1.2.3", directory)

    real = tmp_path / "real"
    real.write_text("external trust\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(tag_signature.TagSignatureError, match="anchor is unavailable"):
        tag_signature.verify(tmp_path, "v1.2.3", link)


def test_verify_binds_the_exact_anchor_and_tag(tmp_path: Path, mocker) -> None:
    """Verify one exact tag through the anchor and the strict Git arguments."""
    anchor = tmp_path / "allowed-signers"
    anchor.write_text("external trust\n", encoding="utf-8")
    run = mocker.patch.object(tag_signature.subprocess, "run")
    tag_signature.verify(tmp_path, "v1.2.3", anchor)
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][-2:] == ("--verify", "refs/tags/v1.2.3")
    assert run.call_args_list[1].args[0][-2:] == ("verify-tag", "v1.2.3")
    assert f"gpg.ssh.allowedSignersFile={anchor.resolve()}" in run.call_args_list[1].args[0]


@pytest.mark.parametrize(
    "failure",
    [OSError("missing"), subprocess.CalledProcessError(1, ["git"], stderr="denied")],
)
def test_verify_translates_one_bounded_failure(tmp_path: Path, failure, mocker) -> None:
    """Report one bounded refusal instead of leaking the transport error."""
    anchor = tmp_path / "allowed-signers"
    anchor.write_text("external trust\n", encoding="utf-8")
    mocker.patch.object(tag_signature.subprocess, "run", side_effect=failure)
    with pytest.raises(tag_signature.TagSignatureError, match="signature is invalid"):
        tag_signature.verify(tmp_path, "v1.2.3", anchor)


def test_command_reports_success_then_fails_closed(tmp_path: Path, mocker, capsys) -> None:
    """Keep one readable success line and exit nonzero on any refusal."""
    anchor = tmp_path / "allowed-signers"
    anchor.write_text("external trust\n", encoding="utf-8")
    run = mocker.patch.object(tag_signature.subprocess, "run")
    args = (str(tmp_path), "v1.2.3", str(anchor))
    tag_signature.main(args)
    assert "signature: OK (v1.2.3)" in capsys.readouterr().out
    assert run.call_count == 2

    run.side_effect = OSError("missing")
    with pytest.raises(SystemExit) as stopped:
        tag_signature.main(args)
    assert stopped.value.code == 1
    assert "signature is invalid" in capsys.readouterr().err
