"""Read bounded native governance reports without reconstructing their policy."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast


class ObservationError(RuntimeError):
    """The original command did not prove the requested repository observation."""


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ObservationError("native governance report is invalid")
    return cast("dict[str, object]", value)


def _payload(report: dict[str, object]) -> dict[str, object]:
    data = _object(report.get("data"))
    if "artifact_reference" not in data:
        return report
    reference = _object(data["artifact_reference"])
    name = reference.get("path")
    if not isinstance(name, str) or not Path(name).is_absolute():
        raise ObservationError("native governance evidence path is invalid")
    path = Path(name)
    if path.is_symlink() or not path.is_file():
        raise ObservationError("native governance evidence is unavailable")
    content = path.read_bytes()
    if reference.get("size_bytes") != len(content) or reference.get("sha256") != (
        "sha256:" + hashlib.sha256(content).hexdigest()
    ):
        raise ObservationError("native governance evidence bytes differ")
    return _object(json.loads(content))


def read(root: Path, *command: str) -> dict[str, object]:
    """Return exact-root native detail; a readable blocked report is not acceptance."""
    root = root.resolve()
    try:
        result = subprocess.run(
            ("ethos", *command, "--root", str(root), "--json"),
            cwd=root,
            stdin=subprocess.DEVNULL,
            timeout=30,
            text=True,
            capture_output=True,
            check=False,
        )
        report = _object(json.loads(result.stdout))
        if result.returncode not in {"pass": (0,), "block": (0, 1)}.get(
            str(report.get("verdict")), ()
        ):
            raise ObservationError("native governance command did not return a valid decision")
        report = _payload(report)
        data = _object(report.get("data"))
        if report.get("command") != " ".join(command) or data.get("root") != str(root):
            raise ObservationError("native governance report subject differs")
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise ObservationError("native governance observation unavailable") from error
