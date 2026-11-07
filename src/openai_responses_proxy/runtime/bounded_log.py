"""Single process-local primitive for bounded, secret-safe text logs."""

from __future__ import annotations

import re
import stat
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

LINE_MAX_BYTES = 1024
_LOCK = threading.Lock()
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:authorization|api[_-]?key|bearer)\s*[:=]?\s*"
        r"(?:bearer\s+)?[^\s,;]+"
    ),
    re.compile(r"\bgAAAA[A-Za-z0-9_-]+"),
)


def redact(message: str, *, max_bytes: int = LINE_MAX_BYTES) -> str:
    """Remove secret-shaped values and bound one caller-controlled message."""
    value = str(message).replace("\r", " ").replace("\n", " ")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    encoded = value.encode("utf-8", "replace")
    if len(encoded) > max_bytes:
        return encoded[:max_bytes].decode("utf-8", "ignore") + " [truncated]"
    return value


def rotate(path: Path, incoming_bytes: int, *, max_bytes: int, backup_count: int) -> int:
    """Rotate regular-file segments and return bytes discarded as oversized."""
    try:
        metadata = path.lstat()
    except OSError:
        return 0
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("log path is not a regular file")
    current_size = metadata.st_size
    if current_size + incoming_bytes <= max_bytes:
        return 0

    discarded = 0
    if current_size > max_bytes:
        path.unlink(missing_ok=True)
        discarded = current_size
    elif backup_count <= 0:
        path.unlink(missing_ok=True)
    else:
        path.with_name(f"{path.name}.{backup_count}").unlink(missing_ok=True)
        for index in range(backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            try:
                size = source.stat().st_size
            except OSError:
                continue
            if size > max_bytes:
                discarded += size
                source.unlink(missing_ok=True)
            else:
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))

    for index in range(1, backup_count + 1):
        segment = path.with_name(f"{path.name}.{index}")
        try:
            size = segment.stat().st_size
            if size > max_bytes:
                discarded += size
                segment.unlink()
        except OSError:
            continue
    return discarded


def append(
    path: Path,
    message: str,
    *,
    max_bytes: int,
    backup_count: int,
    mirror: Callable[[str], object] | None = None,
) -> None:
    """Best-effort append of one timestamped, private, bounded log line."""
    safe_message = redact(message)
    line = _line(safe_message)
    with suppress(OSError), _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        discarded = rotate(
            path,
            len(line.encode("utf-8", "replace")),
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
        if discarded:
            line = _line(f"log_retention_discarded_oversized_bytes={discarded} {safe_message}")
        with path.open("a", encoding="utf-8") as handle:
            with suppress(OSError):
                path.chmod(0o600)
            handle.write(line)
    if mirror is not None:
        with suppress(Exception):
            mirror(line)


def _line(message: str) -> str:
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n"
