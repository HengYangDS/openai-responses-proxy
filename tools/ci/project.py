"""Render and reconcile tracked Forge projections from the CUE owner."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / ".config/ci/pipeline.cue"
MISE = ROOT / ".config/mise/config.toml"


@dataclass(frozen=True, slots=True)
class Projection:
    """One tracked Forge projection and its CUE expression."""

    path: Path
    expression: str


PROJECTIONS = (
    Projection(ROOT / ".gitlab-ci.yml", "gitlab"),
    Projection(ROOT / ".github/workflows/admission.yml", "githubAdmission"),
    Projection(ROOT / ".github/workflows/verify.yml", "githubVerify"),
)


def render(expression: str) -> bytes:
    """Render one expression through the repository-locked CUE executable."""
    result = subprocess.run(
        (
            "mise",
            "exec",
            "--locked",
            "--",
            "cue",
            "export",
            str(MODEL),
            "--expression",
            expression,
            "--out",
            "yaml",
        ),
        check=True,
        cwd=ROOT,
        capture_output=True,
        env={**os.environ, "MISE_CONFIG_FILE": str(MISE)},
    )
    return result.stdout


def reconcile(*, write: bool) -> tuple[str, ...]:
    """Write projections or return paths whose tracked bytes have drifted."""
    drift: list[str] = []
    for projection in PROJECTIONS:
        expected = render(projection.expression)
        if write:
            projection.path.parent.mkdir(parents=True, exist_ok=True)
            projection.path.write_bytes(expected)
        elif not projection.path.is_file() or projection.path.read_bytes() != expected:
            drift.append(projection.path.relative_to(ROOT).as_posix())
    return tuple(drift)


def _command(
    *,
    write: Annotated[bool, Parameter(name="--write", negative=False)] = False,
) -> None:
    """Project Forge files or verify that tracked projections are current."""
    drift = reconcile(write=write)
    if drift:
        raise SystemExit("projection drift: " + ", ".join(drift))


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run projection reconciliation through the repository parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
