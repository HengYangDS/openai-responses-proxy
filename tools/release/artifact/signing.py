"""Shared SSH signing boundary for release asset inventories."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from openai_responses_proxy import product_identity

NAMESPACE = product_identity.RELEASE_NAMESPACE
PRINCIPAL = product_identity.RELEASE_PRINCIPAL


class SignatureError(RuntimeError):
    """Release asset signing or verification failed."""


@contextmanager
def _signing_key(key: Path) -> Generator[Path]:
    """Preserve provider-owned key identity unless POSIX newline repair is needed."""
    content = key.read_bytes()
    if content.endswith(b"\n") or os.name != "posix":
        yield key
        return
    with tempfile.TemporaryDirectory(
        prefix=f"{product_identity.PRODUCT_SLUG}-release-key-"
    ) as name:
        normalized = Path(name) / "key"
        normalized.write_bytes(content + b"\n")
        os.chmod(normalized, 0o600)
        yield normalized


def sign_and_verify(*, assets: Path, key: Path, trust: str) -> None:
    """Sign and verify the canonical checksum inventory with explicit inputs."""
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None or not key.is_file() or key.is_symlink() or not trust.strip():
        raise SignatureError("release signing inputs are unavailable")
    checksums = assets / "SHA256SUMS"
    if not checksums.is_file() or checksums.is_symlink():
        raise SignatureError("release checksum inventory is unavailable")
    signature = assets / "SHA256SUMS.sig"
    signature.unlink(missing_ok=True)
    with _signing_key(key) as signing_key:
        try:
            subprocess.run(
                (
                    ssh_keygen,
                    "-Y",
                    "sign",
                    "-q",
                    "-f",
                    str(signing_key),
                    "-n",
                    NAMESPACE,
                    checksums.name,
                ),
                cwd=assets,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()
            message = "OpenSSH rejected the release signing key"
            raise SignatureError(f"{message}: {detail}" if detail else message) from None
        except (OSError, UnicodeError):
            raise SignatureError("release asset signing failed") from None
    verify(assets=assets, trust=trust)


def verify(*, assets: Path, trust: str) -> None:
    """Verify the canonical checksum signature with one ephemeral trust anchor."""
    ssh_keygen = shutil.which("ssh-keygen")
    checksums, signature = assets / "SHA256SUMS", assets / "SHA256SUMS.sig"
    if (
        ssh_keygen is None
        or not trust.strip()
        or not checksums.is_file()
        or checksums.is_symlink()
        or not signature.is_file()
        or signature.is_symlink()
    ):
        raise SignatureError("release signature inputs are unavailable")
    with tempfile.TemporaryDirectory(
        prefix=f"{product_identity.PRODUCT_SLUG}-release-trust-"
    ) as name:
        anchor = Path(name) / "allowed-signers"
        anchor.write_text(trust.rstrip("\n") + "\n", encoding="utf-8")
        try:
            principal = (
                subprocess.run(
                    (
                        ssh_keygen,
                        "-Y",
                        "find-principals",
                        "-s",
                        str(signature),
                        "-f",
                        str(anchor),
                    ),
                    input=checksums.read_bytes(),
                    check=True,
                    capture_output=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            if principal != PRINCIPAL:
                raise SignatureError("release asset signature principal is invalid")
            subprocess.run(
                (
                    ssh_keygen,
                    "-Y",
                    "verify",
                    "-f",
                    str(anchor),
                    "-I",
                    principal,
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(signature),
                ),
                input=checksums.read_bytes(),
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
            raise SignatureError("release asset signature verification failed") from error
