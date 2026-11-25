"""Installed payload manifest, integrity, and purge ownership."""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openai_responses_proxy.json_value import JsonObject
    from openai_responses_proxy.json_value import ReadOnlyJsonObject


def payload_manifest_path(ctx: runtime_context.RuntimeContext) -> Path:
    """Return the installed payload manifest path."""
    return Path(ctx.payload_dir, inventory.MANIFEST_FILENAME)


def owned_payload_files(ctx: runtime_context.RuntimeContext) -> dict[str, str]:
    """Capture the exact bytes authorized by a complete current payload manifest."""
    install = Path(ctx.payload_dir)
    manifest_path = payload_manifest_path(ctx)
    if manifest_path.is_symlink():
        raise errors.InstallError("installed payload manifest is a symlink")
    if not manifest_path.exists():
        raise errors.InstallError("installed payload manifest is required")
    ok, detail = verify_payload_manifest(ctx)
    if not ok:
        raise errors.InstallError(f"installed payload integrity check failed: {detail}")
    return {
        relative: digest.sha256_file(
            owned_files.regular_file(install, relative, "installed payload purge")
        )
        for relative in sorted(owned_files.current_inventory(install))
    }


def purge_owned_files(root: Path, files: Mapping[str, str]) -> tuple[str, ...]:
    """Resume exact byte removal, preserving replacements and undeclared content."""
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise errors.InstallError("installed payload root is not a real directory")
    remaining_files: dict[str, tuple[tuple[int, int], ...]] = {}
    for relative, expected in files.items():
        target = owned_files.path(root, owned_files.canonical_relative(relative, "payload purge"))
        if not target.exists() and not target.is_symlink():
            continue
        target = owned_files.regular_file(root, relative, "installed payload purge")
        captured = _disposal_identity(root, target)
        if digest.sha256_file(target) != expected or _disposal_identity(root, target) != captured:
            raise errors.InstallError(f"installed payload purge identity changed: {relative}")
        remaining_files[relative] = captured
    for relative in sorted(
        remaining_files, key=lambda value: (-len(PurePosixPath(value).parts), value)
    ):
        target = owned_files.regular_file(root, relative, "installed payload purge")
        if (
            _disposal_identity(root, target) != remaining_files[relative]
            or hashlib.sha256(
                owned_files.read_bytes(target, root=root, label="installed payload purge")
            ).hexdigest()
            != files[relative]
            or _disposal_identity(root, target) != remaining_files[relative]
        ):
            raise errors.InstallError(f"installed payload purge identity changed: {relative}")
        try:
            target.unlink()
        except OSError as exc:
            raise _disposal_failure(relative, exc) from exc
    remove_empty_owned_directories(root, set(files))
    if residual := [
        relative
        for relative in files
        if owned_files.path(root, relative).exists()
        or owned_files.path(root, relative).is_symlink()
    ]:
        raise errors.InstallError("installed payload remains after purge: " + ", ".join(residual))
    return _remaining_paths(root)


def _disposal_failure(relative: str, error: OSError) -> errors.InstallError:
    """Preserve the safe native denial of one failed deletion."""
    return errors.InstallError(
        f"installed payload purge failed: {relative}{errors.os_error_detail(error)}"
    )


def _disposal_identity(root: Path, target: Path) -> tuple[tuple[int, int], ...]:
    """Bind a deletion to its captured file and every owned ancestor."""
    ancestors = (root / parent for parent in target.relative_to(root).parents if parent != Path())
    identities = []
    for path in (root, *ancestors, target):
        metadata = path.lstat()
        identities.append((metadata.st_dev, metadata.st_ino))
    return tuple(identities)


def manifest_serving_payload_sha256(file_digests: Mapping[str, str]) -> str:
    """Return the canonical identity of every manifest-owned serving file."""
    try:
        return inventory.validated_serving_payload_sha256(file_digests)
    except digest.PayloadDigestError as exc:
        raise errors.InstallError(str(exc)) from exc


def manifest_for_digests(
    version: str, file_digests: Mapping[str, str], receipt_sha256: str
) -> JsonObject:
    """Build the canonical current manifest from exact runtime digests."""
    serving_files = dict(file_digests)
    return {
        "schema_version": inventory.MANIFEST_SCHEMA_VERSION,
        "release": version,
        "files": dict(file_digests),
        "serving_files": serving_files,
        "serving_payload_sha256": manifest_serving_payload_sha256(serving_files),
        "release_receipt_sha256": receipt_sha256,
    }


def manifest_bytes(manifest: ReadOnlyJsonObject) -> bytes:
    """Encode a deterministic, human-readable payload manifest."""
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def remove_empty_owned_directories(install: Path, owned: set[str]) -> None:
    """Remove empty directory residue beneath one owned file inventory."""
    directories = {
        parent
        for relative in owned
        for parent in PurePosixPath(relative).parents
        if parent != PurePosixPath(".")
    }
    for relative in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        directory = owned_files.path(install, relative.as_posix())
        if directory.is_symlink() or not directory.exists():
            continue
        if not directory.is_dir():
            raise errors.InstallError(
                f"installed payload directory changed type: {relative.as_posix()}"
            )
        with contextlib.suppress(OSError):
            directory.rmdir()


def _remaining_paths(install: Path) -> tuple[str, ...]:
    if not install.exists():
        return ()
    if install.is_symlink() or not install.is_dir():
        raise errors.InstallError("installed payload root is not a real directory")
    try:
        remaining = tuple(
            sorted(path.relative_to(install).as_posix() for path in install.rglob("*"))
        )
    except OSError as exc:
        raise errors.InstallError("installed payload residue inventory failed") from exc
    if not remaining:
        try:
            install.rmdir()
        except OSError as exc:
            raise errors.InstallError("empty installed payload root removal failed") from exc
    return remaining


def verify_payload_manifest(ctx: runtime_context.RuntimeContext) -> tuple[bool, str]:
    """Verify the installed executable and provider manifest."""
    try:
        manifest = json.loads(payload_manifest_path(ctx).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "installed payload manifest is unavailable"
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != inventory.MANIFEST_SCHEMA_VERSION
    ):
        return False, "manifest schema is unsupported"
    release = manifest.get("release")
    files = manifest.get("files")
    serving_files = manifest.get("serving_files")
    aggregate = manifest.get("serving_payload_sha256")
    receipt_digest = manifest.get("release_receipt_sha256")
    if (
        not isinstance(release, str)
        or not release
        or not isinstance(files, dict)
        or not files
        or not isinstance(serving_files, dict)
        or not serving_files
        or not isinstance(aggregate, str)
        or not isinstance(receipt_digest, str)
    ):
        return False, "manifest is incomplete"
    try:
        expected_files = sorted(owned_files.declared_files(manifest))
        if not inventory.REQUIRED_RUNTIME_FILES.issubset(expected_files) or any(
            not inventory.is_runtime_file(relative) for relative in expected_files
        ):
            return False, "manifest file set mismatch"
    except errors.InstallError as exc:
        return False, str(exc)
    if sorted(files) != sorted(expected_files):
        return False, "manifest file set mismatch"
    if sorted(serving_files) != sorted(expected_files):
        return False, "manifest serving file set mismatch"
    for relative, expected in files.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or len(expected) != digest.SHA256_HEX_LENGTH
        ):
            return False, f"invalid digest: {relative}"
        try:
            actual = digest.sha256_file(Path(ctx.payload_dir, *relative.split("/")))
        except OSError:
            return False, f"installed payload file is unavailable: {relative}"
        if actual != expected:
            return False, f"hash mismatch: {relative}"
    for relative, expected in serving_files.items():
        if files.get(relative) != expected:
            return False, f"serving digest mismatch: {relative}"
    try:
        actual_aggregate = manifest_serving_payload_sha256(serving_files)
    except errors.InstallError as exc:
        return False, str(exc)
    if aggregate != actual_aggregate:
        return False, "serving payload aggregate mismatch"
    if len(receipt_digest) != digest.SHA256_HEX_LENGTH:
        return False, "release receipt digest is invalid"
    try:
        actual_receipt = digest.sha256_file(
            Path(ctx.payload_dir, inventory.RELEASE_RECEIPT_FILENAME)
        )
    except OSError:
        return False, "installed release receipt is unavailable"
    if actual_receipt != receipt_digest:
        return False, "release receipt digest mismatch"
    return True, f"release {release}; {len(files)} files verified"
