"""Collect exact annotated Git tag identity with an external SSH anchor."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.git_environment import immutable_remote_proof_environment
from tools.release import identity


class GitProofError(RuntimeError):
    """An exact signed provider tag could not be proven."""


def collect(*, provider: str, remote: str, tag: str, anchor: Path) -> dict[str, object]:
    """Fetch one exact remote tag into isolation and verify its annotated signature."""
    if provider not in {"gitlab", "github"}:
        raise GitProofError("unknown publication provider")
    if not identity.is_tag(tag):
        raise GitProofError("publication tag must be exact vMAJOR.MINOR.PATCH")
    if not anchor.is_file() or anchor.is_symlink():
        raise GitProofError("publication trust anchor is unavailable")
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        raise GitProofError("ssh-keygen is unavailable")
    ssh_keygen = str(Path(ssh_keygen).resolve())
    git = shutil.which("git")
    if git is None:
        raise GitProofError("git is unavailable")
    git = str(Path(git).resolve())
    environment = immutable_remote_proof_environment()
    try:
        with tempfile.TemporaryDirectory(prefix=f"publication-proof-{provider}-") as workspace_name:
            repository = Path(workspace_name) / "repository"
            _run((git, "init", "-q", "--bare", str(repository)), environment=environment)
            refspec = f"refs/tags/{tag}:refs/tags/{tag}"
            _run(
                (
                    git,
                    "-C",
                    str(repository),
                    "fetch",
                    "--quiet",
                    "--force",
                    "--no-tags",
                    remote,
                    refspec,
                ),
                environment=environment,
            )
            if (
                _output(
                    (git, "-C", str(repository), "cat-file", "-t", f"refs/tags/{tag}"),
                    environment,
                )
                != "tag"
            ):
                raise GitProofError("release tag is not annotated")
            tag_object = _output(
                (git, "-C", str(repository), "cat-file", "tag", f"refs/tags/{tag}"),
                environment,
            )
            headers = tag_object.split("\n\n", 1)[0].splitlines()
            if f"tag {tag}" not in headers or "type commit" not in headers:
                raise GitProofError(
                    "annotated release tag does not directly identify the exact commit"
                )
            verify = (
                git,
                "-C",
                str(repository),
                "-c",
                "gpg.format=ssh",
                "-c",
                f"gpg.ssh.program={ssh_keygen}",
                "-c",
                f"gpg.ssh.allowedSignersFile={anchor.resolve()}",
                "verify-tag",
                tag,
            )
            _run(verify, environment=environment)
            return {
                "provider": provider,
                "tag": tag,
                "tag_object_oid": _output(
                    (git, "-C", str(repository), "rev-parse", f"refs/tags/{tag}"),
                    environment,
                ),
                "commit_oid": _output(
                    (
                        git,
                        "-C",
                        str(repository),
                        "rev-parse",
                        f"refs/tags/{tag}^{{commit}}",
                    ),
                    environment,
                ),
                "tree_oid": _output(
                    (
                        git,
                        "-C",
                        str(repository),
                        "rev-parse",
                        f"refs/tags/{tag}^{{tree}}",
                    ),
                    environment,
                ),
                "anchor_sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
                "signature_verified": True,
            }
    except GitProofError:
        raise
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitProofError("provider tag fetch, verification, or cleanup failed") from error


def _run(
    command: tuple[str, ...], *, environment: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, check=True, capture_output=True, env=environment)


def _output(command: tuple[str, ...], environment: dict[str, str]) -> str:
    try:
        return _run(command, environment=environment).stdout.decode("ascii").strip()
    except UnicodeError as error:
        raise GitProofError("Git object identity is not ASCII") from error
