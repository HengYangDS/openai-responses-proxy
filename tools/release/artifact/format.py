"""Deterministic release assets shared by both Forge publication planes."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

from openai_responses_proxy import product_identity
from tools.release import identity

ARCHIVE_NAME = f"{product_identity.PRODUCT_SLUG}-{{version}}-{{platform}}.tar.gz"
MANIFEST_NAME = f"{product_identity.PRODUCT_SLUG}-{{platform}}.manifest.json"
CHECKSUM_NAME = "SHA256SUMS"
SIGNATURE_NAME = "SHA256SUMS.sig"
RELEASE_PLATFORMS = product_identity.RELEASE_PLATFORMS
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PLATFORM = re.compile(r"^[a-z0-9]+(?:-[a-z0-9_]+)+$")


class AssetError(ValueError):
    """A release asset set is incomplete, ambiguous, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Exact archive bytes and portable permission bits."""

    content: bytes
    mode: int = 0o644


def archive_name(version: str, platform: str) -> str:
    """Return the canonical archive name for one native platform."""
    _validate_identity(version, platform)
    return ARCHIVE_NAME.format(version=version, platform=platform)


def manifest_name(platform: str) -> str:
    """Return the canonical machine-manifest name for one native platform."""
    _validate_identity("0.0.0", platform)
    return MANIFEST_NAME.format(platform=platform)


def archive_bytes(files: Mapping[str, bytes | ArchiveFile], version: str, platform: str) -> bytes:
    """Build one reproducible native archive from exact named payload bytes."""
    _validate_identity(version, platform)
    prefix = f"{product_identity.PRODUCT_SLUG}-{version}-{platform}"
    output = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for relative, raw_file in sorted(files.items()):
            file = raw_file if isinstance(raw_file, ArchiveFile) else ArchiveFile(raw_file)
            content = file.content
            path = PurePosixPath(relative)
            windows_path = PureWindowsPath(relative)
            if (
                path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in path.parts
                or ".." in windows_path.parts
                or not path.parts
            ):
                raise AssetError("release asset path escapes the source root")
            info = tarfile.TarInfo(f"{prefix}/{path.as_posix()}")
            info.size = len(content)
            if file.mode not in {0o644, 0o755}:
                raise AssetError("release asset mode is invalid")
            info.mode = file.mode
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def asset_manifest(
    *,
    version: str,
    platform: str,
    archive_name: str,
    archive: bytes,
    files: Mapping[str, bytes | ArchiveFile],
) -> bytes:
    """Bind one native archive to its platform and complete internal inventory."""
    expected_archive = globals()["archive_name"](version, platform)
    if archive_name != expected_archive or not files:
        raise AssetError("release asset identity is invalid")
    inventory = {
        name: hashlib.sha256(
            content.content if isinstance(content, ArchiveFile) else content
        ).hexdigest()
        for name, content in sorted(files.items())
        if _safe_relative(name)
    }
    if len(inventory) != len(files):
        raise AssetError("release asset path escapes the archive root")
    document = {
        "schema_version": 1,
        "product": product_identity.PRODUCT_SLUG,
        "version": version,
        "platform": platform,
        "archive": archive_name,
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "files": inventory,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def verify_platform_archive(archive: bytes, manifest: bytes) -> dict[str, object]:
    """Verify archive bytes and every member against one machine manifest."""
    try:
        decoded: object = json.loads(manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssetError("release asset manifest is malformed") from error
    if not isinstance(decoded, dict) or set(decoded) != {
        "schema_version",
        "product",
        "version",
        "platform",
        "archive",
        "archive_sha256",
        "files",
    }:
        raise AssetError("release asset manifest is malformed")
    document: dict[str, object] = {}
    for key, value in decoded.items():
        if not isinstance(key, str):
            raise AssetError("release asset manifest is malformed")
        document[key] = value
    version, platform = document.get("version"), document.get("platform")
    if not isinstance(version, str) or not isinstance(platform, str):
        raise AssetError("release asset manifest is malformed")
    expected_archive = archive_name(version, platform)
    files = document.get("files")
    schema_version = document.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or document.get("product") != product_identity.PRODUCT_SLUG
        or document.get("archive") != expected_archive
        or document.get("archive_sha256") != hashlib.sha256(archive).hexdigest()
        or not isinstance(files, dict)
        or not files
        or any(
            not isinstance(name, str)
            or not _safe_relative(name)
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            for name, digest in files.items()
        )
    ):
        raise AssetError("release asset manifest is malformed or inconsistent")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = bundle.getmembers()
            prefix = f"{product_identity.PRODUCT_SLUG}-{version}-{platform}/"
            actual: dict[str, str] = {}
            for member in members:
                if not member.isfile() or not member.name.startswith(prefix):
                    raise AssetError("release archive contains an invalid member")
                relative = member.name.removeprefix(prefix)
                if not _safe_relative(relative) or relative in actual:
                    raise AssetError("release archive contains an invalid member")
                expected_mode = (
                    0o755
                    if relative
                    in {
                        f"bin/{product_identity.executable_name(windows=False)}",
                        f"bin/{product_identity.executable_name(windows=True)}",
                    }
                    else 0o644
                )
                if member.mode != expected_mode:
                    raise AssetError("release archive member mode is invalid")
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise AssetError("release archive contains an unreadable member")
                actual[relative] = hashlib.sha256(extracted.read()).hexdigest()
    except (tarfile.TarError, OSError, EOFError) as error:
        raise AssetError("release archive is malformed") from error
    if actual != files:
        raise AssetError("release archive contents differ from its manifest")
    return document


def checksums(assets: Mapping[str, bytes]) -> bytes:
    """Return a stable GNU-style SHA-256 manifest for named assets."""
    if not assets:
        raise AssetError("release asset set must not be empty")
    return "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(assets.items())
    ).encode()


def parse_checksums(content: bytes) -> dict[str, str]:
    """Parse a strict checksum manifest without permitting duplicate names."""
    parsed: dict[str, str] = {}
    try:
        lines = content.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise AssetError("release checksum manifest is not ASCII") from error
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not _DIGEST.fullmatch(parts[0]) or not parts[1]:
            raise AssetError("release checksum manifest is malformed")
        name = parts[1]
        if PurePosixPath(name).name != name or PureWindowsPath(name).name != name or name in parsed:
            raise AssetError("release checksum asset name is invalid or duplicated")
        parsed[name] = parts[0]
    if not parsed:
        raise AssetError("release checksum manifest is empty")
    return parsed


def verify(assets: Mapping[str, bytes], manifest: bytes) -> dict[str, str]:
    """Require a manifest to name every non-manifest asset with its exact digest."""
    expected = parse_checksums(manifest)
    actual = {name: hashlib.sha256(content).hexdigest() for name, content in assets.items()}
    if expected != actual:
        raise AssetError("release assets do not match SHA256SUMS")
    return expected


def release_digests(
    files: Mapping[str, bytes],
    version: str,
    platforms: tuple[str, ...],
    *,
    require_signature: bool = True,
) -> dict[str, str]:
    """Verify and identify the complete native multi-platform release asset set."""
    if not platforms or len(set(platforms)) != len(platforms):
        raise AssetError("release platforms must be unique and nonempty")
    archive_names = {archive_name(version, platform) for platform in platforms}
    manifest_names = {manifest_name(platform) for platform in platforms}
    expected_names = archive_names | manifest_names | {CHECKSUM_NAME}
    if require_signature:
        expected_names.add(SIGNATURE_NAME)
    if set(files) != expected_names:
        raise AssetError("release asset set is incomplete or contains unknown files")
    signed_assets = expected_names - {CHECKSUM_NAME, SIGNATURE_NAME}
    verify({name: files[name] for name in signed_assets}, files[CHECKSUM_NAME])
    for platform in platforms:
        document = verify_platform_archive(
            files[archive_name(version, platform)], files[manifest_name(platform)]
        )
        if document["version"] != version or document["platform"] != platform:
            raise AssetError("release manifest differs from the selected version or platform")
    return {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}


def release_asset_names(
    version: str, platforms: tuple[str, ...], *, require_signature: bool = True
) -> set[str]:
    """Return the exact public asset-name contract for selected platforms."""
    if not platforms or len(set(platforms)) != len(platforms):
        raise AssetError("release platforms must be unique and nonempty")
    names = {
        *(archive_name(version, platform) for platform in platforms),
        *(manifest_name(platform) for platform in platforms),
        CHECKSUM_NAME,
    }
    if require_signature:
        names.add(SIGNATURE_NAME)
    return names


def release_platforms(names: set[str], version: str) -> tuple[str, ...]:
    """Return the exact nonempty platform inventory encoded by asset names."""
    prefix = f"{product_identity.PRODUCT_SLUG}-{version}-"
    suffix = ".tar.gz"
    platforms = tuple(
        sorted(
            name.removeprefix(prefix).removesuffix(suffix)
            for name in names
            if name.startswith(prefix) and name.endswith(suffix)
        )
    )
    if not platforms or names != release_asset_names(version, platforms):
        raise AssetError("release asset set is incomplete or contains unknown files")
    return platforms


def _safe_relative(value: str) -> bool:
    path, windows_path = PurePosixPath(value), PureWindowsPath(value)
    return bool(path.parts) and not (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
        or ".." in windows_path.parts
    )


def _validate_identity(version: str, platform: str) -> None:
    if not identity.is_version(version) or _PLATFORM.fullmatch(platform) is None:
        raise AssetError("release version or platform identity is invalid")
