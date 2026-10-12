"""Native user-command projection contracts."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from pathlib import PureWindowsPath

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from tests.lifecycle.fixtures import install_context


def test_posix_command_path_uses_absolute_xdg_bin_home_or_user_local_bin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    explicit = tmp_path / "commands"

    assert command.path(str(home), {"XDG_BIN_HOME": str(explicit)}, windows=False) == (
        explicit / "openai-responses-proxy"
    )
    assert command.path(str(home), {}, windows=False) == (
        home / ".local" / "bin" / "openai-responses-proxy"
    )
    with pytest.raises(errors.InstallError, match="XDG_BIN_HOME must be absolute"):
        command.path(str(home), {"XDG_BIN_HOME": "relative/bin"}, windows=False)


def test_windows_command_path_uses_the_user_application_alias_directory() -> None:
    home = PureWindowsPath("C:/Users/example")

    projected = command.path(
        str(home),
        {"LOCALAPPDATA": str(home / "AppData" / "Local")},
        windows=True,
    )

    assert PureWindowsPath(projected) == (
        home / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "openai-responses-proxy.cmd"
    )


def assert_native_projection(command_path: Path, target: Path) -> None:
    """Assert the selected projection owns the exact installed executable."""
    expected_kind = "launcher" if command_path.suffix.casefold() == ".cmd" else "symlink"
    assert command.status(command_path, target) == {
        "state": "owned",
        "kind": expected_kind,
        "path": str(command_path),
    }


def test_projection_replaces_only_absent_or_exact_owned_link(tmp_path: Path) -> None:
    ctx = install_context(tmp_path)
    target = Path(ctx.executable)
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / "openai-responses-proxy"

    command.project(command_path, target)
    assert_native_projection(command_path, target)

    foreign = tmp_path / "foreign"
    foreign.write_text("foreign", encoding="utf-8")
    command_path.unlink()
    command_path.symlink_to(foreign)
    with pytest.raises(errors.InstallError, match="occupied by another owner") as raised:
        command.project(command_path, target)
    assert str(command_path) not in str(raised.value)
    assert command_path.resolve() == foreign.resolve()


def test_projection_preserves_an_already_owned_link(tmp_path: Path, *, mocker) -> None:
    """An unchanged control-plane projection does not churn link identity."""
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"executable")
    command_path = tmp_path / "commands" / command.COMMAND_NAME
    command.project(command_path, target)
    before = command_path.lstat()
    replace = mocker.patch.object(command.os, "replace")

    command.project(command_path, target)

    replace.assert_not_called()
    after = command_path.lstat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_restore_and_remove_preserve_a_path_that_changed_ownership(
    tmp_path: Path,
) -> None:
    target = tmp_path / "payload" / "openai-responses-proxy"
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / "openai-responses-proxy"
    snapshot = command.snapshot(command_path, target)
    command.project(command_path, target)

    command.restore(command_path, target, snapshot)
    assert not command_path.exists()

    command.project(command_path, target)
    command_path.unlink()
    command_path.write_text("foreign", encoding="utf-8")
    with pytest.raises(errors.InstallError, match="changed ownership"):
        command.remove(command_path, target)
    assert command_path.read_text(encoding="utf-8") == "foreign"


def test_restore_rechecks_foreign_ownership_after_admission(tmp_path: Path, *, mocker) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir()
    target.write_bytes(b"runtime")
    command_path = tmp_path / "commands" / command.COMMAND_NAME
    previous = command.Snapshot(
        state="owned",
        path=str(command_path),
        target=str(target),
        kind="hardlink",
        device=1,
        inode=2,
    )
    classify = mocker.patch.object(
        command, "_classify", side_effect=[("absent", ""), ("foreign", "")]
    )
    mocker.patch.object(command, "_matches_snapshot", return_value=False)
    replacement = mocker.patch.object(command, "_restore_snapshot")

    with pytest.raises(errors.InstallError, match="command path changed ownership"):
        command.restore(command_path, target, previous)

    assert classify.call_count == 2
    replacement.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior")
def test_status_reports_exact_owned_link(tmp_path: Path) -> None:
    target = tmp_path / "payload" / "openai-responses-proxy"
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / "openai-responses-proxy"
    command.project(command_path, target)

    assert command.status(command_path, target) == {
        "state": "owned",
        "kind": "symlink",
        "path": str(command_path),
    }


def test_snapshot_round_trip_and_rejects_foreign_or_invalid_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / command.COMMAND_NAME

    absent = command.snapshot(command_path, target)
    command.write_snapshot(tmp_path, absent)
    assert command.read_snapshot(tmp_path) == absent

    command.project(command_path, target)
    owned = command.snapshot(command_path, target)
    command.write_snapshot(tmp_path, owned)
    assert command.read_snapshot(tmp_path) == owned

    command_path.unlink()
    command_path.write_text("foreign", encoding="utf-8")
    with pytest.raises(errors.InstallError, match="occupied by another owner"):
        command.snapshot(command_path, target)

    invalid = tmp_path / command.SNAPSHOT_FILENAME
    invalid.write_bytes(
        command.digest.canonical_json(
            {
                "schema_version": command.SNAPSHOT_SCHEMA,
                "state": "absent",
                "path": str(command_path),
                "target": str(target),
                "kind": "symlink",
                "device": 0,
                "inode": 0,
            }
        )
    )
    with pytest.raises(errors.InstallError, match="command snapshot is invalid"):
        command.read_snapshot(tmp_path)


def test_project_rejects_invalid_target_and_reports_native_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    command_path = tmp_path / "commands" / command.COMMAND_NAME

    with pytest.raises(errors.InstallError, match="target is not a regular file"):
        command.project(command_path, target)

    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    linked_target = tmp_path / "linked-target"
    linked_target.symlink_to(target)
    with pytest.raises(errors.InstallError, match="target is not a regular file"):
        command.project(command_path, linked_target)

    def fail_projection(_target: Path, _link: Path) -> None:
        raise OSError("projection unavailable")

    monkeypatch.setattr(command.os, "symlink", fail_projection)
    with pytest.raises(errors.InstallError, match="native command projection failed") as raised:
        command.project(command_path, target)
    assert str(command_path) not in str(raised.value)
    assert not list(command_path.parent.glob(".*.tmp-*"))


def test_windows_projection_launches_the_complete_onedir_payload(
    tmp_path: Path,
) -> None:
    target = tmp_path / "payload" / f"{command.COMMAND_NAME}.exe"
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / f"{command.COMMAND_NAME}.cmd"

    command.project(command_path, target)

    assert command_path.read_bytes() == command.windows_launcher(target)
    assert command.status(command_path, target) == {
        "state": "owned",
        "kind": "launcher",
        "path": str(command_path),
    }


def test_windows_transition_replaces_and_restores_the_legacy_executable_link(
    tmp_path: Path,
) -> None:
    legacy_target = tmp_path / "legacy" / f"{command.COMMAND_NAME}.exe"
    current_target = tmp_path / "current" / f"{command.COMMAND_NAME}.exe"
    legacy_target.parent.mkdir(parents=True)
    current_target.parent.mkdir(parents=True)
    legacy_target.write_bytes(b"legacy onedir executable")
    current_target.write_bytes(b"current onedir executable")
    command_root = tmp_path / "WindowsApps"
    legacy_command = command_root / f"{command.COMMAND_NAME}.exe"
    current_command = command_root / f"{command.COMMAND_NAME}.cmd"
    command_root.mkdir()
    os.link(legacy_target, legacy_command)
    previous = command.snapshot(legacy_command, legacy_target)

    command.project(current_command, current_target, previous=previous)

    assert not legacy_command.exists()
    assert current_command.read_bytes() == command.windows_launcher(current_target)

    command.restore(current_command, current_target, previous)

    assert not current_command.exists()
    assert os.path.samefile(legacy_command, legacy_target)


def test_windows_transition_restores_the_previous_launcher_when_projection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_target = tmp_path / "legacy" / f"{command.COMMAND_NAME}.exe"
    current_target = tmp_path / "current" / f"{command.COMMAND_NAME}.exe"
    legacy_target.parent.mkdir(parents=True)
    current_target.parent.mkdir(parents=True)
    legacy_target.write_bytes(b"legacy onedir executable")
    current_target.write_bytes(b"current onedir executable")
    command_root = tmp_path / "WindowsApps"
    legacy_command = command_root / f"legacy-{command.COMMAND_NAME}.cmd"
    current_command = command_root / f"{command.COMMAND_NAME}.cmd"
    command.project(legacy_command, legacy_target)
    previous = command.snapshot(legacy_command, legacy_target)
    real_write_bytes = Path.write_bytes

    def fail_current_projection(self: Path, data: bytes) -> int:
        if self.parent == command_root and self.name.startswith(f".{current_command.name}.tmp-"):
            raise OSError("projection unavailable")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_current_projection)

    with pytest.raises(errors.InstallError, match="native command projection failed"):
        command.project(current_command, current_target, previous=previous)

    assert not current_command.exists()
    assert legacy_command.read_bytes() == command.windows_launcher(legacy_target)


def test_projection_and_restore_reject_changed_snapshot_ownership(
    tmp_path: Path,
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    previous_path = tmp_path / "commands" / f"legacy-{command.COMMAND_NAME}"
    previous_path.parent.mkdir(parents=True)
    previous_target = tmp_path / "prior" / command.COMMAND_NAME
    previous_target.parent.mkdir()
    previous_target.write_text("prior owner", encoding="utf-8")
    previous_path.symlink_to(previous_target)
    previous = command.snapshot(previous_path, previous_target)
    previous_path.unlink()
    previous_path.write_text("changed owner", encoding="utf-8")

    with pytest.raises(errors.InstallError, match="changed ownership"):
        command.project(tmp_path / "commands" / command.COMMAND_NAME, target, previous=previous)

    current = tmp_path / "current" / command.COMMAND_NAME
    current.parent.mkdir(parents=True)
    current.write_text("foreign", encoding="utf-8")
    absent = command.Snapshot(state="absent", path=str(current), target=str(target))
    with pytest.raises(errors.InstallError, match="changed ownership"):
        command.restore(current, target, absent)


def test_project_requires_post_projection_ownership_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / command.COMMAND_NAME
    observations = iter((("absent", ""), ("foreign", "")))
    monkeypatch.setattr(command, "_classify", lambda *_args: next(observations))

    with pytest.raises(errors.InstallError, match="ownership is unproved"):
        command.project(command_path, target)


def test_detach_handles_absence_snapshot_ownership_and_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / command.COMMAND_NAME
    absent = command.snapshot(command_path, target)
    command.detach(command_path, target, absent)

    command_path.parent.mkdir(parents=True)
    command_path.symlink_to(target)
    prior = command.snapshot(command_path, target)
    command.detach(command_path, target, prior)
    assert not command_path.exists()

    command_path.write_text("changed owner", encoding="utf-8")
    with pytest.raises(errors.InstallError, match="changed ownership") as raised:
        command.detach(command_path, target, replace(prior, device=-1))
    assert str(command_path) not in str(raised.value)

    command_path.unlink()
    command.project(command_path, target)
    real_unlink = Path.unlink

    def fail_owned_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == command_path:
            raise OSError("busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_owned_unlink)
    with pytest.raises(errors.InstallError, match="native command removal failed") as raised:
        command.detach(command_path, target, command.snapshot(command_path, target))
    assert str(command_path) not in str(raised.value)


def test_restore_and_remove_cover_owned_absent_and_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "payload" / command.COMMAND_NAME
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "commands" / command.COMMAND_NAME

    command.project(command_path, target)
    owned = command.snapshot(command_path, target)
    command.restore(command_path, target, owned)
    assert_native_projection(command_path, target)
    assert command.remove(command_path, target) is True
    assert command.remove(command_path, target) is False

    command.project(command_path, target)
    real_unlink = Path.unlink

    def fail_owned_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == command_path:
            raise OSError("busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_owned_unlink)
    with pytest.raises(errors.InstallError, match="native command removal failed"):
        command.remove(command_path, target)


def test_classification_and_snapshot_matching_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.write_text("runtime", encoding="utf-8")
    command_path = tmp_path / "command"
    command_path.write_text("foreign", encoding="utf-8")

    assert command._classify(command_path, target) == ("foreign", "")
    monkeypatch.setattr(
        command.os.path, "samefile", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    assert command._classify(command_path, target) == ("foreign", "")

    assert command._matches_snapshot(command_path, None) is False
    assert (
        command._matches_snapshot(
            command_path,
            command.Snapshot(state="absent", path=str(command_path), target=str(target)),
        )
        is False
    )
    command_path.unlink()
    command_path.symlink_to(target)
    owned = command.snapshot(command_path, target)
    assert command._matches_snapshot(command_path, owned) is True
    assert command._matches_snapshot(command_path, replace(owned, inode=owned.inode + 1)) is False

    real_lstat = Path.lstat

    def fail_lstat(self: Path) -> os.stat_result:
        if self == command_path:
            raise OSError("unavailable")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    assert command._matches_snapshot(command_path, owned) is False
