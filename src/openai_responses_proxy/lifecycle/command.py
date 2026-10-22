"""Native user-command path and exact projection ownership."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from pathlib import PureWindowsPath

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.serving import digest

COMMAND_NAME = product_identity.COMMAND_NAME
SNAPSHOT_FILENAME = "command.json"
SNAPSHOT_SCHEMA = 2


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Pre-transaction command-path state."""

    state: str
    path: str
    target: str
    kind: str = ""
    device: int = 0
    inode: int = 0


def path(home: str, environment: Mapping[str, str], *, windows: bool) -> Path:
    """Return the current user's platform-native command path."""
    if windows:
        local = environment.get("LOCALAPPDATA") or str(PureWindowsPath(home) / "AppData" / "Local")
        return Path(
            str(PureWindowsPath(local) / "Microsoft" / "WindowsApps" / f"{COMMAND_NAME}.cmd")
        )
    configured = environment.get("XDG_BIN_HOME", "")
    if configured:
        directory = Path(configured).expanduser()
        if not directory.is_absolute():
            raise errors.InstallError("XDG_BIN_HOME must be absolute")
    else:
        directory = Path(home) / ".local" / "bin"
    return directory / COMMAND_NAME


def snapshot(command_path: Path, target: Path) -> Snapshot:
    """Prove the command path is absent or owned before payload mutation."""
    state, kind = _classify(command_path, target)
    if state == "foreign":
        raise errors.InstallError("command path is occupied by another owner")
    if state == "absent":
        return Snapshot(state="absent", path=str(command_path), target=str(target))
    metadata = command_path.lstat()
    return Snapshot(
        state="owned",
        path=str(command_path),
        target=str(target),
        kind=kind,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def write_snapshot(root: Path, value: Snapshot) -> None:
    """Persist the command rollback fact in the transaction-owned directory."""
    owned_files.write_bytes(
        root / SNAPSHOT_FILENAME,
        snapshot_bytes(value),
        mode=0o600,
    )


def snapshot_bytes(value: Snapshot) -> bytes:
    """Encode the exact rollback carrier before it is written or disposed."""
    return digest.canonical_json(
        {
            "schema_version": SNAPSHOT_SCHEMA,
            "state": value.state,
            "path": value.path,
            "target": value.target,
            "kind": value.kind,
            "device": value.device,
            "inode": value.inode,
        }
    )


def read_snapshot(root: Path, *, expected_sha256: str | None = None) -> Snapshot:
    """Read one canonical rollback fact with its declared byte identity."""
    value = owned_files.read_canonical_json(
        root / SNAPSHOT_FILENAME, "command snapshot", expected_sha256=expected_sha256
    )
    state = value.get("state")
    path = value.get("path")
    target = value.get("target")
    kind = value.get("kind")
    device = value.get("device")
    inode = value.get("inode")
    if (
        value.get("schema_version") != SNAPSHOT_SCHEMA
        or not isinstance(state, str)
        or state not in {"absent", "owned"}
        or not isinstance(path, str)
        or not path
        or not Path(path).is_absolute()
        or not isinstance(target, str)
        or not target
        or not Path(target).is_absolute()
        or not isinstance(kind, str)
        or kind not in {"", "symlink", "hardlink", "launcher"}
        or type(device) is not int
        or type(inode) is not int
        or (state == "absent" and any((kind, device, inode)))
        or (state == "owned" and not all((kind, device, inode)))
    ):
        raise errors.InstallError("command snapshot is invalid")
    return Snapshot(
        state=state,
        path=path,
        target=target,
        kind=kind,
        device=device,
        inode=inode,
    )


def project(command_path: Path, target: Path, previous: Snapshot | None = None) -> None:
    """Atomically expose the complete installed runtime through one command."""
    if target.is_symlink() or not target.is_file():
        raise errors.InstallError("installed command target is not a regular file")
    previous_path = Path(previous.path) if previous is not None else command_path
    if previous is not None and previous_path != command_path and previous.state == "owned":
        _remove_snapshot(previous_path, previous)
    state, _kind = _classify(command_path, target)
    if state == "owned":
        return
    if state == "foreign" and not _matches_snapshot(command_path, previous):
        raise errors.InstallError("command path is occupied by another owner")
    command_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = command_path.with_name(f".{command_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        if command_path.suffix.casefold() == ".cmd":
            temporary.write_bytes(windows_launcher(target))
        else:
            os.symlink(target, temporary)
        os.replace(temporary, command_path)
    except OSError as exc:
        if previous is not None and previous_path != command_path and previous.state == "owned":
            _restore_snapshot(previous_path, Path(previous.target), previous)
        raise errors.InstallError("native command projection failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    if _classify(command_path, target)[0] != "owned":
        raise errors.InstallError("native command projection ownership is unproved")


def require_detachable(command_path: Path, target: Path, previous: Snapshot) -> str:
    """Admit candidate removal without changing the command projection."""
    state, _kind = _classify(command_path, target)
    if state == "foreign" and not _matches_snapshot(command_path, previous):
        raise errors.InstallError("command path changed ownership")
    return state


def detach(command_path: Path, target: Path, previous: Snapshot) -> None:
    """Remove only the current candidate link or the proved prior link."""
    if require_detachable(command_path, target, previous) == "absent":
        return
    try:
        command_path.unlink()
    except OSError as exc:
        raise errors.InstallError("native command removal failed") from exc


def require_restorable(command_path: Path, target: Path, previous: Snapshot) -> None:
    """Admit prior-link restoration without changing the command projection."""
    previous_path = Path(previous.path)
    if previous.state == "owned" and _matches_snapshot(previous_path, previous):
        return
    if _classify(command_path, target)[0] == "foreign":
        raise errors.InstallError("command path changed ownership")


def restore(command_path: Path, target: Path, previous: Snapshot) -> None:
    """Restore the exact prior link state after payload restoration."""
    require_restorable(command_path, target, previous)
    previous_path = Path(previous.path)
    if previous.state == "owned" and _matches_snapshot(previous_path, previous):
        return
    state, _kind = _classify(command_path, target)
    if state == "foreign":
        raise errors.InstallError("command path changed ownership")
    if state == "owned":
        command_path.unlink()
    if previous.state == "owned":
        _restore_snapshot(previous_path, Path(previous.target), previous)


def remove(command_path: Path, target: Path) -> bool:
    """Remove only a live command link still owned by this payload."""
    state, _kind = _classify(command_path, target)
    if state == "absent":
        return False
    if state == "foreign":
        raise errors.InstallError("command path changed ownership")
    try:
        command_path.unlink()
    except OSError as exc:
        raise errors.InstallError("native command removal failed") from exc
    return True


def status(command_path: Path, target: Path) -> dict[str, object]:
    """Return one read-only command discoverability result."""
    state, kind = _classify(command_path, target)
    return {
        "state": state,
        "kind": kind or None,
        "path": str(command_path),
    }


def _classify(command_path: Path, target: Path) -> tuple[str, str]:
    if not os.path.lexists(command_path):
        return "absent", ""
    try:
        if command_path.is_symlink():
            return (
                ("owned", "symlink")
                if command_path.resolve(strict=False) == target.resolve(strict=False)
                else ("foreign", "")
            )
        if (
            command_path.suffix.casefold() == ".cmd"
            and command_path.is_file()
            and command_path.read_bytes() == windows_launcher(target)
        ):
            return "owned", "launcher"
        if command_path.is_file() and target.is_file() and os.path.samefile(command_path, target):
            return "owned", "hardlink"
    except OSError:
        pass
    return "foreign", ""


def windows_launcher(target: Path) -> bytes:
    """Return the canonical Windows command that preserves an onedir payload."""
    executable = str(target).replace("%", "%%")
    return f'@echo off\r\n@"{executable}" %*\r\n'.encode()


def _remove_snapshot(command_path: Path, previous: Snapshot) -> None:
    if not _matches_snapshot(command_path, previous):
        raise errors.InstallError("command path changed ownership")
    try:
        command_path.unlink()
    except OSError as exc:
        raise errors.InstallError("native command removal failed") from exc


def _restore_snapshot(command_path: Path, target: Path, previous: Snapshot) -> None:
    command_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = command_path.with_name(f".{command_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        if previous.kind == "launcher":
            temporary.write_bytes(windows_launcher(target))
        elif previous.kind == "hardlink":
            os.link(target, temporary)
        elif previous.kind == "symlink":
            os.symlink(target, temporary)
        else:
            raise errors.InstallError("command snapshot kind is invalid")
        os.replace(temporary, command_path)
    except OSError as exc:
        raise errors.InstallError("native command restoration failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _matches_snapshot(command_path: Path, previous: Snapshot | None) -> bool:
    if (
        previous is None
        or previous.state != "owned"
        or Path(previous.path) != command_path
        or not os.path.lexists(command_path)
    ):
        return False
    try:
        metadata = command_path.lstat()
    except OSError:
        return False
    return (
        metadata.st_dev == previous.device
        and metadata.st_ino == previous.inode
        and _classify(command_path, Path(previous.target)) == ("owned", previous.kind)
    )
