"""Symlink-safe paths, file I/O, and inventories for owned payload bytes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import is_json_object
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory

OWNED_PAYLOAD_METADATA = (
    inventory.MANIFEST_FILENAME,
    inventory.RELEASE_RECEIPT_FILENAME,
    inventory.RUNTIME_CONFIG_FILENAME,
)


def declared_files(manifest: JsonObject) -> frozenset[str]:
    """Return the canonical payload inventory declared by one manifest."""
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise errors.InstallError("installed payload manifest file set is invalid")
    return frozenset(canonical_relative(value, "installed payload") for value in files)


def current_inventory(root: Path) -> frozenset[str]:
    """Return current manifest-owned payload and metadata paths."""
    manifest = read_json_object(root / inventory.MANIFEST_FILENAME, "installed payload manifest")
    return declared_files(manifest) | frozenset(OWNED_PAYLOAD_METADATA)


def path(root: Path, relative: str) -> Path:
    """Return one canonical POSIX payload path beneath ``root``."""
    return root.joinpath(*PurePosixPath(relative).parts)


def canonical_relative(value: object, label: str) -> str:
    """Validate and return one canonical owned relative path."""
    if not isinstance(value, str) or not value:
        raise errors.InstallError(f"{label} path must be a non-empty string")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or PureWindowsPath(value).drive
        or value.startswith("//")
        or relative.as_posix() != value
        or value.endswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise errors.InstallError(f"{label} path is not canonical: {value!r}")
    return value


def regular_file(root: Path, relative: str, label: str) -> Path:
    """Return one existing owned regular file without following symlinks."""
    relative = canonical_relative(relative, label)
    try:
        if root.is_symlink() or not root.is_dir():
            raise errors.InstallError(f"{label} root is not a real directory")
        current = root
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise errors.InstallError(f"{label} path has a symlink ancestor: {relative}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise errors.InstallError(f"{label} path ancestor is not a directory: {relative}")
        target = current / parts[-1]
        metadata = target.lstat()
    except FileNotFoundError as exc:
        raise errors.InstallError(f"{label} file is unavailable: {relative}") from exc
    except OSError as exc:
        raise errors.InstallError(f"{label} path is unavailable: {relative}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise errors.InstallError(f"{label} path is a symlink: {relative}")
    if not stat.S_ISREG(metadata.st_mode):
        raise errors.InstallError(f"{label} path is not a regular file: {relative}")
    return target


def read_bytes(target: Path, *, root: Path, label: str) -> bytes:
    """Read an unchanged regular-file identity without following a substituted link."""
    relative = target.relative_to(root).as_posix()
    target = regular_file(root, relative, label)
    try:
        expected = target.lstat()
        descriptor = os.open(
            target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(descriptor, "rb") as stream:
            observed = os.fstat(stream.fileno())
            if not stat.S_ISREG(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
                expected.st_dev,
                expected.st_ino,
            ):
                raise errors.InstallError(f"{label} file identity changed")
            content = stream.read()
            current = regular_file(root, relative, label).lstat()
            if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
                raise errors.InstallError(f"{label} file identity changed")
            return content
    except OSError as exc:
        raise errors.InstallError(f"{label} file is unavailable or changed") from exc


def write_bytes(
    target: Path, content: bytes, *, mode: int = 0o644, root: Path | None = None
) -> None:
    """Atomically write one owned regular file through symlink-safe ancestors."""
    root = root or target.parent
    _real_parent(target, root)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise errors.InstallError(f"owned path is not a regular file: {target.relative_to(root)}")
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _real_parent(target, root)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise errors.InstallError(f"owned path changed type: {target.relative_to(root)}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_object(target: Path, label: str) -> JsonObject:
    """Read one JSON object with a bounded installation error."""
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise errors.InstallError(f"{label} is unavailable or invalid") from exc
    if not is_json_object(value):
        raise errors.InstallError(
            f"{label} is not a JSON object with finite values and string keys"
        )
    return value


def read_canonical_json(
    target: Path, label: str, *, expected_sha256: str | None = None
) -> JsonObject:
    """Read one unchanged canonical object, checking declared byte identity first."""
    try:
        content = read_bytes(target, root=target.parent, label=label)
    except (OSError, errors.InstallError) as exc:
        raise errors.InstallError(f"{label} is unavailable or invalid") from exc
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise errors.InstallError(f"{label} identity changed")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise errors.InstallError(f"{label} is unavailable or invalid") from exc
    if not is_json_object(value) or digest.canonical_json(value) != content:
        raise errors.InstallError(f"{label} is not canonical JSON")
    return value


def _real_parent(target: Path, root: Path) -> None:
    if root.is_symlink():
        raise errors.InstallError("installed payload root is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise errors.InstallError("installed payload root is not a real directory")
    current = root
    try:
        relatives = target.relative_to(root).parts[:-1]
    except ValueError as exc:
        raise errors.InstallError("owned path escapes its root") from exc
    for part in relatives:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise errors.InstallError(
                f"owned path has a symlink ancestor: {target.relative_to(root)}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise errors.InstallError(
                f"owned path ancestor is not a directory: {target.relative_to(root)}"
            )
