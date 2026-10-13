"""Private executable roles and process identity owned by the serving domain."""

from __future__ import annotations

import sys
from pathlib import Path

LISTENER_MODE = "--internal-listener"
WATCHDOG_MODE = "--internal-watchdog"
HANDOFF_CHILD_MODE = "--internal-handoff-child"
PREWARM_MODE = "--internal-prewarm"


def current_executable() -> str:
    """Return the exact product executable used to start this process."""
    selected = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    try:
        return str(Path(selected).resolve(strict=True))
    except OSError as error:
        raise RuntimeError("running product executable is unavailable") from error
