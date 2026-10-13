"""Opaque verified native artifact consumed by one installation transaction."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import override

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.json_value import FrozenJsonObject
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.json_value import is_json_object
from openai_responses_proxy.json_value import thaw_value
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory

RECEIPT_SCHEMA = 1
_ARCHIVE = re.compile(
    rf"^{re.escape(product_identity.PRODUCT_SLUG)}-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-"
    r"(?P<platform>[a-z0-9]+(?:-[a-z0-9_]+)+)\.tar\.gz$"
)
_NAMESPACE = product_identity.RELEASE_NAMESPACE
_MAX_ASSET_BYTES = 512 * 1024 * 1024


class ArtifactError(RuntimeError):
    """Report an invalid, mutated, forged, or already consumed native artifact."""


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """One immutable regular file admitted from a verified native archive."""

    path: str
    mode: Literal["100644", "100755"]
    blob_oid: str
    sha256: str
    content: bytes


class _ArtifactAuthority:
    """Process-local issuer and one-use claimant for verified artifacts."""

    def __init__(self) -> None:
        self.token = object()
        self.issued: weakref.WeakSet[VerifiedArtifact] = weakref.WeakSet()

    def mint(
        self,
        blobs: tuple[ArtifactFile, ...],
        receipt: ReadOnlyJsonObject,
        sidecar: ReadOnlyJsonObject,
    ) -> VerifiedArtifact:
        candidate = VerifiedArtifact(
            blobs=blobs, receipt=receipt, sidecar=sidecar, _token=self.token
        )
        self.issued.add(candidate)
        return candidate

    def claim(
        self, candidate: object
    ) -> tuple[tuple[ArtifactFile, ...], str, str, FrozenJsonObject, FrozenJsonObject]:
        if type(candidate) is not VerifiedArtifact or candidate not in self.issued:
            raise ArtifactError("installation requires an admitted VerifiedArtifact")
        candidate._verify_integrity()
        return (
            candidate._claim_blobs(),
            candidate.version,
            candidate.receipt_sha256,
            candidate.receipt,
            candidate.sidecar,
        )


_AUTHORITY = _ArtifactAuthority()


class VerifiedArtifact:
    """Opaque single-use authority over one exact verified native artifact."""

    __slots__ = (
        "__weakref__",
        "_blobs",
        "_claimed",
        "_receipt",
        "_receipt_sha256",
        "_sidecar",
    )

    _blobs: tuple[ArtifactFile, ...]
    _claimed: bool
    _receipt: FrozenJsonObject
    _receipt_sha256: str
    _sidecar: FrozenJsonObject

    def __init__(
        self,
        *,
        blobs: tuple[ArtifactFile, ...],
        receipt: ReadOnlyJsonObject,
        sidecar: ReadOnlyJsonObject,
        _token: object | None = None,
    ) -> None:
        """Construct an admitted artifact through the private authority token."""
        if _token is not _AUTHORITY.token:
            raise TypeError("VerifiedArtifact is opaque; use signed asset admission")
        object.__setattr__(self, "_blobs", blobs)
        object.__setattr__(self, "_receipt", digest.freeze_mapping(receipt))
        object.__setattr__(self, "_sidecar", digest.freeze_mapping(sidecar))
        object.__setattr__(
            self,
            "_receipt_sha256",
            hashlib.sha256(digest.canonical_json(receipt)).hexdigest(),
        )
        object.__setattr__(self, "_claimed", False)

    @override
    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation of admitted artifact identity."""
        raise ArtifactError("verified artifact capability is immutable")

    @property
    def version(self) -> str:
        """Return the admitted strict release version."""
        return str(self._receipt["version"])

    @property
    def serving_payload_sha256(self) -> str:
        """Return the aggregate digest of admitted serving files."""
        return str(self._receipt["serving_payload_sha256"])

    @property
    def receipt_sha256(self) -> str:
        """Return the digest of the canonical receipt bytes."""
        return self._receipt_sha256

    @property
    def receipt(self) -> FrozenJsonObject:
        """Return immutable signed-artifact evidence."""
        return self._receipt

    @property
    def sidecar(self) -> FrozenJsonObject:
        """Return immutable receipt-integrity evidence."""
        return self._sidecar

    def peek_blobs(self) -> tuple[ArtifactFile, ...]:
        """Return immutable bytes for read-only identity checks."""
        return self._blobs

    def _claim_blobs(self) -> tuple[ArtifactFile, ...]:
        if self._claimed:
            raise ArtifactError("verified artifact bytes were already claimed")
        object.__setattr__(self, "_claimed", True)
        return self._blobs

    def _verify_integrity(self) -> None:
        receipt_digest = hashlib.sha256(
            digest.canonical_json(plain_value(self._receipt))
        ).hexdigest()
        entries = self._receipt.get("payload")
        bound = ("serving_payload_sha256",)
        if (
            receipt_digest != self._receipt_sha256
            or self._sidecar.get("schema_version") != RECEIPT_SCHEMA
            or self._sidecar.get("algorithm") != "sha256"
            or self._sidecar.get("receipt_sha256") != receipt_digest
            or any(self._sidecar.get(field) != self._receipt.get(field) for field in bound)
            or not isinstance(entries, tuple)
            or len(entries) != len(self._blobs)
        ):
            raise ArtifactError("verified artifact integrity check failed")
        for entry, blob in zip(entries, self._blobs, strict=True):
            expected = {
                "path": blob.path,
                "mode": blob.mode,
                "blob_oid": blob.blob_oid,
                "sha256": hashlib.sha256(blob.content).hexdigest(),
            }
            if (
                not isinstance(entry, Mapping)
                or dict(entry) != expected
                or blob.sha256 != expected["sha256"]
            ):
                raise ArtifactError("verified artifact integrity check failed")
        serving_paths = self._receipt.get("serving_files")
        if not isinstance(serving_paths, tuple):
            raise ArtifactError("verified artifact integrity check failed")
        try:
            actual = inventory.validated_serving_payload_sha256(
                {blob.path: blob.sha256 for blob in self._blobs if blob.path in serving_paths}
            )
        except digest.PayloadDigestError as error:
            raise ArtifactError("verified artifact integrity check failed") from error
        if actual != self._receipt.get("serving_payload_sha256"):
            raise ArtifactError("verified artifact integrity check failed")


def mint(
    blobs: tuple[ArtifactFile, ...],
    receipt: ReadOnlyJsonObject,
    sidecar: ReadOnlyJsonObject,
) -> VerifiedArtifact:
    """Mint one process-local capability after signed-asset verification."""
    return _AUTHORITY.mint(blobs, receipt, sidecar)


def claim(candidate: object):
    """Consume one live admitted verified-artifact authority."""
    return _AUTHORITY.claim(candidate)


def plain_value(value: object) -> object:
    """Copy frozen payload evidence into canonical JSON-compatible values."""
    return thaw_value(value)


def admit(asset: Path, *, trust_anchor: Path) -> VerifiedArtifact:
    """Admit one archive whose checksum manifest has an authorized SSH signature."""
    if not asset.is_file():
        raise errors.InstallError("native release archive is unavailable")
    archive = asset.resolve(strict=True)
    if not archive.is_file() or archive.stat().st_size > _MAX_ASSET_BYTES:
        raise errors.InstallError("native release archive is unavailable or too large")
    match = _ARCHIVE.fullmatch(archive.name)
    if match is None:
        raise errors.InstallError("native release archive name is invalid")
    manifest_path = archive.with_name(
        f"{product_identity.PRODUCT_SLUG}-{match['platform']}.manifest.json"
    )
    checksums_path = archive.with_name("SHA256SUMS")
    signature_path = archive.with_name("SHA256SUMS.sig")
    if not trust_anchor.is_file():
        raise errors.InstallError("release trust anchor is unavailable")
    anchor = trust_anchor.resolve(strict=True)
    for path, label in (
        (manifest_path, "platform manifest"),
        (checksums_path, "checksum manifest"),
        (signature_path, "checksum signature"),
        (anchor, "trust anchor"),
    ):
        if not path.is_file():
            raise errors.InstallError(f"release {label} is unavailable")
    checksums = checksums_path.read_bytes()
    _verify_signature(checksums, signature_path, anchor)
    archive_bytes = archive.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    checksum_map = _parse_checksums(checksums)
    expected = {
        archive.name: hashlib.sha256(archive_bytes).hexdigest(),
        manifest_path.name: hashlib.sha256(manifest_bytes).hexdigest(),
    }
    if any(checksum_map.get(name) != sha256 for name, sha256 in expected.items()):
        raise errors.InstallError("release assets do not match signed SHA256SUMS")
    document = _verify_archive(archive_bytes, manifest_bytes, match.groupdict())
    blobs = _archive_blobs(archive_bytes, document)
    receipt = {
        "schema_version": 1,
        "verification_scope": "signed-native-release-asset",
        "version": match["version"],
        "platform": match["platform"],
        "archive": archive.name,
        "archive_sha256": expected[archive.name],
        "checksum_manifest_sha256": hashlib.sha256(checksums).hexdigest(),
        "trust_anchor_sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
        "serving_files": [blob.path for blob in blobs],
        "serving_payload_sha256": inventory.validated_serving_payload_sha256(
            {blob.path: blob.sha256 for blob in blobs}
        ),
        "payload": [
            {
                "path": blob.path,
                "mode": blob.mode,
                "blob_oid": blob.blob_oid,
                "sha256": blob.sha256,
            }
            for blob in blobs
        ],
    }
    receipt_sha256 = hashlib.sha256(digest.canonical_json(receipt)).hexdigest()
    sidecar = {
        "schema_version": 1,
        "algorithm": "sha256",
        "receipt_sha256": receipt_sha256,
        "serving_payload_sha256": receipt["serving_payload_sha256"],
    }
    return mint(blobs, receipt, sidecar)


def _verify_signature(content: bytes, signature: Path, trust_anchor: Path) -> None:
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise errors.InstallError("ssh-keygen is required to verify release assets")
    found = subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "find-principals",
            "-s",
            str(signature),
            "-f",
            str(trust_anchor),
        ],
        input=content,
        capture_output=True,
        check=False,
    )
    principals = tuple(line for line in found.stdout.decode(errors="replace").splitlines() if line)
    if found.returncode or len(principals) != 1:
        raise errors.InstallError("release signature has no unique authorized principal")
    verified = subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "verify",
            "-f",
            str(trust_anchor),
            "-I",
            principals[0],
            "-n",
            _NAMESPACE,
            "-s",
            str(signature),
        ],
        input=content,
        capture_output=True,
        check=False,
    )
    if verified.returncode:
        raise errors.InstallError("release signature verification failed")


def _parse_checksums(content: bytes) -> dict[str, str]:
    parsed: dict[str, str] = {}
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise errors.InstallError("release checksum manifest is malformed") from exc
    for line in lines:
        parts = line.split("  ", 1)
        if (
            len(parts) != 2
            or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None
            or Path(parts[1]).name != parts[1]
            or parts[1] in parsed
        ):
            raise errors.InstallError("release checksum manifest is malformed")
        parsed[parts[1]] = parts[0]
    return parsed


def _verify_archive(archive: bytes, manifest: bytes, identity: dict[str, str]) -> JsonObject:
    try:
        document = json.loads(manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.InstallError("release platform manifest is malformed") from exc
    required = {
        "schema_version",
        "product",
        "version",
        "platform",
        "archive",
        "archive_sha256",
        "files",
    }
    if (
        not is_json_object(document)
        or set(document) != required
        or document.get("schema_version") != 1
        or document.get("product") != product_identity.PRODUCT_SLUG
        or document.get("version") != identity["version"]
        or document.get("platform") != identity["platform"]
        or document.get("archive")
        != f"{product_identity.PRODUCT_SLUG}-{identity['version']}-{identity['platform']}.tar.gz"
        or document.get("archive_sha256") != hashlib.sha256(archive).hexdigest()
        or not isinstance(document.get("files"), dict)
    ):
        raise errors.InstallError("release platform manifest is inconsistent")
    return document


def _archive_blobs(archive: bytes, document: JsonObject) -> tuple[ArtifactFile, ...]:
    platform = str(document["platform"])
    version = str(document["version"])
    raw_files = document["files"]
    if not isinstance(raw_files, dict) or not all(
        isinstance(path, str) and isinstance(file_digest, str)
        for path, file_digest in raw_files.items()
    ):
        raise errors.InstallError("release archive inventory is invalid")
    files = dict(raw_files)
    executable_name = (
        f"bin/{product_identity.executable_name(windows=True)}"
        if platform.startswith("windows-")
        else f"bin/{product_identity.executable_name(windows=False)}"
    )
    expected = set(files)
    if (
        not expected
        or not {executable_name, "providers.toml", "LICENSE"}.issubset(expected)
        or any(
            relative != "LICENSE"
            and not inventory.is_runtime_file(relative, windows=platform.startswith("windows-"))
            for relative in expected
        )
    ):
        raise errors.InstallError("release archive inventory is invalid")
    prefix = f"{product_identity.PRODUCT_SLUG}-{version}-{platform}/"
    blobs: list[ArtifactFile] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) != len(expected):
                raise errors.InstallError("release archive inventory is invalid")
            seen: set[str] = set()
            for member in members:
                if not member.isfile() or not member.name.startswith(prefix):
                    raise errors.InstallError("release archive contains an invalid member")
                relative = member.name.removeprefix(prefix)
                if relative not in expected or relative in seen:
                    raise errors.InstallError("release archive contains an invalid member")
                seen.add(relative)
                stream = bundle.extractfile(member)
                content = b"" if stream is None else stream.read()
                expected_digest = files.get(relative)
                if hashlib.sha256(content).hexdigest() != expected_digest:
                    raise errors.InstallError("release archive member digest mismatch")
                if relative == "LICENSE":
                    continue
                target = relative
                expected_mode = 0o755 if target == executable_name else 0o644
                if member.mode != expected_mode:
                    raise errors.InstallError("release archive member mode is invalid")
                digest_value = hashlib.sha256(content).hexdigest()
                blobs.append(
                    ArtifactFile(
                        path=target,
                        mode="100755" if expected_mode == 0o755 else "100644",
                        blob_oid=digest_value,
                        sha256=digest_value,
                        content=content,
                    )
                )
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise errors.InstallError("release archive is malformed") from exc
    return tuple(sorted(blobs, key=lambda blob: blob.path))
