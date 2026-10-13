"""Installed payload manifest, integrity, and purge ownership."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from pathlib import PurePosixPath

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.service import digest
from openai_responses_proxy.service import inventory

PAYLOAD_MANIFEST_SCHEMA_VERSION = 2
_WINDOWS_DISPOSAL_ERRORS = frozenset({5, 32})
_DISPOSAL_RETRY_SECONDS = 5.0


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
    retry_deadline = None
    for relative in sorted(
        remaining_files, key=lambda value: (-len(PurePosixPath(value).parts), value)
    ):
        last_error = None
        while True:
            if (
                last_error is not None
                and retry_deadline is not None
                and time.monotonic() >= retry_deadline
            ):
                raise _disposal_failure(relative, last_error) from last_error
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
            if (
                last_error is not None
                and retry_deadline is not None
                and time.monotonic() >= retry_deadline
            ):
                raise _disposal_failure(relative, last_error) from last_error
            try:
                target.unlink()
                break
            except OSError as exc:
                # Windows can deny a delete while a native image or external
                # handle is still mapped; only those codes share one bounded wait.
                if getattr(exc, "winerror", None) in _WINDOWS_DISPOSAL_ERRORS:
                    now = time.monotonic()
                    if retry_deadline is None:
                        retry_deadline = now + _DISPOSAL_RETRY_SECONDS
                    remaining = retry_deadline - now
                    if remaining > 0:
                        last_error = exc
                        time.sleep(min(0.05, remaining))
                        continue
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
    """Preserve the last safe native denial when the retry budget is exhausted."""
    code = getattr(error, "winerror", None) or error.errno
    return errors.InstallError(f"installed payload purge failed: {relative} (OS error {code})")


def _disposal_identity(root: Path, target: Path) -> tuple[tuple[int, int], ...]:
    """Bind a deletion retry to its captured file and every owned ancestor."""
    ancestors = (
        root / parent for parent in target.relative_to(root).parents if parent != Path(".")
    )
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
        "schema_version": PAYLOAD_MANIFEST_SCHEMA_VERSION,
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
        try:
            directory.rmdir()
        except OSError:
            pass


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


def _write_payload_manifest_for_fixture(
    ctx: runtime_context.RuntimeContext,
    *,
    release_receipt_sha256: str | None = None,
) -> Path:
    """Write the production manifest shape for a current test payload."""
    install = Path(ctx.payload_dir)
    windows = (install / inventory.WINDOWS_EXECUTABLE).is_file()
    paths = sorted(inventory.required_runtime_files(windows=windows))
    digests = {relative: digest.sha256_file(Path(ctx.payload_dir, relative)) for relative in paths}
    manifest: JsonObject = {
        "schema_version": PAYLOAD_MANIFEST_SCHEMA_VERSION,
        "release": "0.0.0",
        "files": digests,
        "serving_files": dict(digests),
        "serving_payload_sha256": manifest_serving_payload_sha256(digests),
    }
    if release_receipt_sha256 is not None:
        manifest["release_receipt_sha256"] = release_receipt_sha256
    path = payload_manifest_path(ctx)
    owned_files.write_bytes(path, manifest_bytes(manifest))
    return path


def verify_payload_manifest(ctx: runtime_context.RuntimeContext) -> tuple[bool, str]:
    """Verify the installed executable and provider manifest."""
    try:
        manifest = json.loads(payload_manifest_path(ctx).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "installed payload manifest is unavailable"
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != PAYLOAD_MANIFEST_SCHEMA_VERSION
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
    ):
        return False, "manifest is incomplete"
    try:
        expected_files = sorted(owned_files.declared_files(manifest))
        required = inventory.required_runtime_files(
            windows=inventory.WINDOWS_EXECUTABLE in expected_files
        )
        if not required.issubset(expected_files) or any(
            not inventory.is_runtime_file(
                relative, windows=inventory.WINDOWS_EXECUTABLE in expected_files
            )
            for relative in expected_files
        ):
            return False, "manifest file set mismatch"
    except errors.InstallError as exc:
        return False, str(exc)
    if sorted(files) != sorted(expected_files):
        return False, "manifest file set mismatch"
    if sorted(serving_files) != sorted(expected_files):
        return False, "manifest serving file set mismatch"
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str) or len(expected) != 64:
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
    if receipt_digest is not None:
        if not isinstance(receipt_digest, str) or len(receipt_digest) != 64:
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
