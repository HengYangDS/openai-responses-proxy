"""The native secret scanner distinguishes public fingerprints from credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.repository_toolchain
@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("credential", [False, True])
@pytest.mark.parametrize("newline", [False, True])
def test_public_signature_fingerprint_never_hides_a_credential(
    tmp_path: Path, encoded: bool, credential: bool, newline: bool
) -> None:
    fingerprint = base64.b64encode(hashlib.sha256(b"public signing-key fixture").digest())
    status = (
        'Good "git" signature for author@example.test with ED25519 key SHA256:'
        + fingerprint.decode().rstrip("=")
        + ("\n" if newline else "")
    )
    document = {"status": status}
    if credential:
        token = hashlib.sha256(b"fabricated API credential fixture").digest()
        document["api_key"] = base64.urlsafe_b64encode(token).decode().rstrip("=")
    payload = json.dumps(document).encode()
    if encoded:
        payload = json.dumps({"content_base64": base64.b64encode(payload).decode()}).encode()
    report = tmp_path / "gitleaks.json"
    scanned = subprocess.run(
        (
            "gitleaks",
            "stdin",
            "--config",
            str(ROOT / ".config/checks/gitleaks/gitleaks.toml"),
            "--redact",
            "--no-banner",
            "--no-color",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--timeout",
            "15",
        ),
        input=payload,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        timeout=20,
    )
    assert scanned.returncode == int(credential)
    findings = json.loads(report.read_bytes())
    assert [finding["RuleID"] for finding in findings] == (
        ["generic-api-key"] if credential else []
    )
