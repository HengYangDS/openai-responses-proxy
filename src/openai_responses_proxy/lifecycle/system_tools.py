"""Resolve the host tools that lifecycle trusts from system locations, never a caller's PATH.

Release verification and native supervision run ``ssh-keygen``, ``systemctl``, ``launchctl``
and ``plutil``. A PATH lookup would let any directory earlier in the caller's PATH supply the
binary that decides whether an asset is accepted or a service is installed. These tools therefore
resolve only from fixed system locations.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from pathlib import PurePath
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from openai_responses_proxy import errors

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

# POSIX system tools live in these directories; NixOS links its system profile's tools from
# /run/current-system/sw/bin. No directory comes from the environment.
TRUSTED_POSIX_DIRECTORIES = (
    PurePosixPath("/usr/bin"),
    PurePosixPath("/bin"),
    PurePosixPath("/usr/sbin"),
    PurePosixPath("/sbin"),
    PurePosixPath("/run/current-system/sw/bin"),
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
    directories: Iterable[PurePath] = TRUSTED_POSIX_DIRECTORIES,
    trusted: Callable[[Path], bool] = _root_owned_and_closed,
) -> str | None:
    """Return the first trusted system executable of that name, or None when none is trusted.

    A candidate counts only if it is an executable regular file and its directory, the
    candidate, the file it resolves to and that file's directory are all trusted, which by
    default means owned by root and not writable by group or others. A system profile such as
    NixOS's links its tools into another directory, which must be trusted as well.
    """
    for declared in directories:
        directory = Path(declared)
        candidate = directory / name
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        try:
            target = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if all(trusted(path) for path in (directory, candidate, target, target.parent)):
            return str(candidate)
    return None


def ssh_keygen() -> str:
    """Return the trusted ssh-keygen that verifies release-asset signatures."""
    if (resolved := posix_tool("ssh-keygen")) is not None:
        return resolved
    raise errors.InstallError("ssh-keygen is required to verify release assets")
