"""Verify one local product tag against an explicit peer trust anchor."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cyclopts import App

from tools.release import identity


class TagSignatureError(RuntimeError):
    """A product tag or its trust input is invalid."""


def verify(repository: Path, tag: str, anchor: Path) -> None:
    """Verify one exact product tag with one explicit external anchor."""
    if not identity.is_tag(tag):
        raise TagSignatureError(f"release tag must be v<semver>: {tag}")
    if not anchor.is_file() or anchor.is_symlink():
        raise TagSignatureError("product release trust anchor is unavailable")
    try:
        subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "--verify", f"refs/tags/{tag}"),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(repository),
                "-c",
                "gpg.format=ssh",
                "-c",
                "gpg.ssh.program=ssh-keygen",
                "-c",
                f"gpg.ssh.allowedSignersFile={anchor.resolve()}",
                "verify-tag",
                tag,
            ),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise TagSignatureError("product release tag signature is invalid") from error


def _command(repository: Path, tag: str, anchor: Path) -> None:
    """Verify one product tag without implicit trust sources."""
    try:
        verify(repository.resolve(), tag, anchor)
    except TagSignatureError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    print(f"product release tag signature: OK ({tag})")


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run tag verification through the repository parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
