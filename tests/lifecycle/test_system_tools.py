"""Host tools resolve only from trusted system locations, never from a caller's PATH."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import system_tools

if TYPE_CHECKING:
    import ctypes

    from pytest_mock import MockerFixture


def _tool(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_bytes(b"#!/bin/sh\n")
    tool.chmod(0o755)
    return tool


def _trust_all(_path: Path) -> bool:
    return True


def test_a_nixos_system_profile_tool_is_found_after_the_usual_directories(
    tmp_path: Path,
) -> None:
    usual = tmp_path / "usr/bin"
    usual.mkdir(parents=True)
    profile = _tool(tmp_path / "run/current-system/sw/bin", "systemctl")

    resolved = system_tools.posix_tool(
        "systemctl", directories=(usual, profile.parent), trusted=_trust_all
    )

    assert resolved == str(profile)


def test_the_first_trusted_directory_wins(tmp_path: Path) -> None:
    first = _tool(tmp_path / "usr/bin", "ssh-keygen")
    _tool(tmp_path / "run/current-system/sw/bin", "ssh-keygen")

    resolved = system_tools.posix_tool(
        "ssh-keygen",
        directories=(first.parent, tmp_path / "run/current-system/sw/bin"),
        trusted=_trust_all,
    )

    assert resolved == str(first)


@pytest.mark.parametrize("untrusted", ["directory", "file"])
def test_a_tool_in_an_untrusted_location_is_refused(tmp_path: Path, untrusted: str) -> None:
    tool = _tool(tmp_path / "usr/bin", "systemctl")
    refused = tool.parent if untrusted == "directory" else tool

    resolved = system_tools.posix_tool(
        "systemctl", directories=(tool.parent,), trusted=lambda path: path != refused
    )

    assert resolved is None


def test_a_trusted_link_into_a_writable_directory_is_refused(tmp_path: Path) -> None:
    writable = tmp_path / "home/user/bin"
    target = _tool(writable, "systemctl")
    trusted_bin = tmp_path / "usr/bin"
    trusted_bin.mkdir(parents=True)
    (trusted_bin / "systemctl").symlink_to(target)

    resolved = system_tools.posix_tool(
        "systemctl", directories=(trusted_bin,), trusted=lambda path: path != writable
    )

    assert resolved is None


def test_a_link_into_a_trusted_directory_is_accepted(tmp_path: Path) -> None:
    target = _tool(tmp_path / "run/current-system/sw/bin", "systemctl")
    trusted_bin = tmp_path / "usr/bin"
    trusted_bin.mkdir(parents=True)
    link = trusted_bin / "systemctl"
    link.symlink_to(target)

    resolved = system_tools.posix_tool("systemctl", directories=(trusted_bin,), trusted=_trust_all)

    assert resolved == str(link)


def test_a_dangling_link_is_refused(tmp_path: Path) -> None:
    trusted_bin = tmp_path / "usr/bin"
    trusted_bin.mkdir(parents=True)
    (trusted_bin / "systemctl").symlink_to(tmp_path / "absent/systemctl")

    resolved = system_tools.posix_tool("systemctl", directories=(trusted_bin,), trusted=_trust_all)

    assert resolved is None


def test_every_checked_location_is_the_resolved_link_and_its_directory(tmp_path: Path) -> None:
    target = _tool(tmp_path / "nix/store/abc-systemd/bin", "systemctl")
    trusted_bin = tmp_path / "usr/bin"
    trusted_bin.mkdir(parents=True)
    (trusted_bin / "systemctl").symlink_to(target)
    checked: list[Path] = []

    def record(path: Path) -> bool:
        checked.append(path)
        return True

    system_tools.posix_tool("systemctl", directories=(trusted_bin,), trusted=record)

    assert checked == [
        trusted_bin,
        trusted_bin / "systemctl",
        target.resolve(strict=True),
        target.resolve(strict=True).parent,
    ]


def test_the_default_trust_check_refuses_a_group_or_world_writable_location(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    tool = _tool(tmp_path / "bin", "launchctl")
    owner = mocker.Mock(st_uid=0)
    for mode, trusted in ((0o40755, True), (0o40775, False), (0o40757, False)):
        owner.st_mode = mode
        mocker.patch.object(Path, "stat", return_value=owner)
        assert system_tools._root_owned_and_closed(tool.parent) is trusted


def test_the_default_trust_check_refuses_a_location_root_does_not_own(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    tool = _tool(tmp_path / "bin", "plutil")
    mocker.patch.object(Path, "stat", return_value=mocker.Mock(st_uid=501, st_mode=0o40755))

    assert system_tools._root_owned_and_closed(tool.parent) is False


def test_a_missing_or_non_executable_candidate_is_skipped(tmp_path: Path) -> None:
    directory = tmp_path / "usr/bin"
    directory.mkdir(parents=True)
    (directory / "systemctl").write_bytes(b"not executable")

    assert (
        system_tools.posix_tool("systemctl", directories=(directory,), trusted=_trust_all) is None
    )
    assert system_tools.posix_tool("absent", directories=(directory,), trusted=_trust_all) is None


def test_resolution_never_consults_the_callers_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = _tool(tmp_path / "planted", "systemctl")
    monkeypatch.setenv("PATH", str(planted.parent))

    resolved = system_tools.posix_tool(
        "systemctl", directories=(tmp_path / "usr/bin",), trusted=_trust_all
    )

    assert resolved is None


def test_the_trusted_directories_are_fixed_system_locations() -> None:
    expected = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/run/current-system/sw/bin")
    assert tuple(map(str, system_tools.TRUSTED_POSIX_DIRECTORIES)) == expected


def test_posix_ssh_keygen_fails_closed_when_no_trusted_copy_exists(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(system_tools.os, "name", "posix")
    resolve = mocker.patch.object(system_tools, "posix_tool", return_value=None)

    with pytest.raises(
        errors.InstallError, match="ssh-keygen is required to verify release assets"
    ):
        system_tools.ssh_keygen()

    resolve.assert_called_once_with("ssh-keygen")


def test_windows_ssh_keygen_is_the_system_directory_openssh_copy(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    system = tmp_path / "System32"
    keygen = _tool(system / "OpenSSH", "ssh-keygen.exe")
    mocker.patch.object(system_tools.os, "name", "nt")
    mocker.patch.object(system_tools, "windows_system_directory", return_value=system)

    assert system_tools.ssh_keygen() == str(keygen)

    keygen.unlink()
    with pytest.raises(
        errors.InstallError, match="ssh-keygen is required to verify release assets"
    ):
        system_tools.ssh_keygen()


def test_the_windows_system_directory_comes_from_the_kernel(mocker: MockerFixture) -> None:
    system = r"C:\Windows\System32"

    def system_directory(buffer: ctypes.Array[ctypes.c_wchar], size: int) -> int:
        del size
        buffer.value = system
        return len(system)

    kernel32 = mocker.Mock(GetSystemDirectoryW=system_directory)
    load = mocker.Mock(return_value=kernel32)
    mocker.patch.object(system_tools.ctypes, "WinDLL", load, create=True)

    assert system_tools.windows_system_directory() == Path(system)
    load.assert_called_once_with("kernel32", use_last_error=True)
