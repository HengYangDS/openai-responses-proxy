"""Validate tracked text files against the repository editor contract."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / ".config/checks/text-layout/policy.toml"


def _tracked(root: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(root), "--git-dir=.git", "--work-tree=.", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(sorted(path.decode() for path in completed.stdout.split(b"\0") if path))


def audit(root: Path = ROOT, policy_path: Path = DEFAULT_POLICY) -> tuple[str, ...]:
    """Return stable layout gaps for tracked text surfaces."""
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    suffixes = frozenset(policy["tracked_suffixes"])
    names = frozenset(policy["tracked_names"])
    gaps: list[str] = []
    for relative in _tracked(root):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        if path.name not in names and path.suffix.lower() not in suffixes:
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode(policy["encoding"])
        except UnicodeDecodeError:
            gaps.append(f"text_encoding_invalid:{relative}")
            continue
        if policy["line_ending"] == "lf" and b"\r" in raw:
            gaps.append(f"text_line_ending_invalid:{relative}")
        if policy["insert_final_newline"] and raw and not raw.endswith(b"\n"):
            gaps.append(f"text_final_newline_missing:{relative}")
        if policy["trim_trailing_whitespace"]:
            for number, line in enumerate(text.splitlines(), 1):
                if line.rstrip(" \t") != line:
                    gaps.append(f"text_trailing_whitespace:{relative}:{number}")
    return tuple(gaps)


def _command(
    *,
    policy: Annotated[Path, Parameter(name="--policy")] = DEFAULT_POLICY,
) -> None:
    """Validate the repository text layout."""
    gaps = audit(policy_path=policy)
    if gaps:
        raise SystemExit("\n".join(gaps))


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run the text-layout gate through the repository parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
