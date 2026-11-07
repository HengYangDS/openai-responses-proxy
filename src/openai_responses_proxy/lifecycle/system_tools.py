"""Resolve the host tools that lifecycle trusts from system locations, never a caller's PATH.

Release verification and native supervision run ``ssh-keygen``, ``systemctl``, ``launchctl``,
``plutil`` and ``schtasks``. A PATH lookup would let any directory earlier in the caller's PATH,
or on Windows the current directory, supply the binary that decides whether an asset is accepted
or a service is installed. These tools therefore resolve only from fixed system locations.
"""

from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

from openai_responses_proxy import errors

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

# POSIX system tools live in these directories; NixOS links its system profile's tools from
# /run/current-system/sw/bin. No directory comes from the environment.
TRUSTED_POSIX_DIRECTORIES = (
    Path("/usr/bin"),
    Path("/bin"),
    Path("/usr/sbin"),
    Path("/sbin"),
    Path("/run/current-system/sw/bin"),
)
_UNSAFE_WRITERS = stat.S_IWGRP | stat.S_IWOTH


def _root_owned_and_closed(path: Path) -> bool:
    """Return whether root owns the path and neither group nor others may write it."""
    try:
        status = path.stat()
    except OSError:
        return False
    return status.st_uid == 0 and not status.st_mode & _UNSAFE_WRITERS


def posix_tool(
    name: str,
    *,
    directories: Iterable[Path] = TRUSTED_POSIX_DIRECTORIES,
    trusted: Callable[[Path], bool] = _root_owned_and_closed,
) -> str | None:
    """Return the first trusted system executable of that name, or None when none is trusted.

    A candidate counts only if it is an executable regular file and both it and its directory
    are trusted, which by default means owned by root and not writable by group or others.
    """
    for directory in directories:
        candidate = directory / name
        if (
            candidate.is_file()
            and os.access(candidate, os.X_OK)
            and trusted(directory)
            and trusted(candidate)
        ):
            return str(candidate)
    return None


def windows_system_directory() -> Path:
    """Return the Windows system directory that the kernel reports, never a search path."""
    load_library: object = getattr(ctypes, "WinDLL", None)
    if not callable(load_library):
        raise errors.UnsupportedPlatformError("Windows system libraries are unavailable")
    kernel32 = cast("Callable[..., ctypes.CDLL]", load_library)("kernel32", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise errors.InstallError("Windows system directory is unavailable")
    return Path(buffer.value)


def ssh_keygen() -> str:
    """Return the trusted ssh-keygen that verifies release-asset signatures."""
    if os.name == "nt":
        candidate = windows_system_directory() / "OpenSSH" / "ssh-keygen.exe"
        if candidate.is_file():
            return str(candidate)
    elif (resolved := posix_tool("ssh-keygen")) is not None:
        return resolved
    raise errors.InstallError("ssh-keygen is required to verify release assets")
