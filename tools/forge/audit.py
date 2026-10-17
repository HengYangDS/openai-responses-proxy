"""Read-only exact-object parity evidence for optional Forge peers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

from openai_responses_proxy import product_identity
from tools.git_environment import isolated_config_environment
from tools.governance import observation

ROOT = Path(__file__).resolve().parents[2]


def command(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a captured subprocess and raise a concise error when requested."""
    result = subprocess.run(
        args,
        cwd=cwd,
        env=isolated_config_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "command failed")
    return result


def output(*args: str, cwd: Path = ROOT) -> str:
    """Return stripped standard output for a successful command."""
    return command(*args, cwd=cwd).stdout.strip()


def branches_for_audit(root: Path = ROOT) -> tuple[frozenset[str], frozenset[str]]:
    """Read persistent roles from the native topology owner, not retired bindings."""
    document = observation.read(root, "lane", "status")
    roles = document.get("role_policy")
    if not isinstance(roles, dict):
        raise RuntimeError("repository branch-role policy is unavailable or invalid")
    release = roles.get("release_branch")
    accepted = roles.get("accepted_branch")
    candidate = roles.get("candidate_branch")
    if not all(isinstance(branch, str) and branch for branch in (release, accepted, candidate)):
        raise RuntimeError("repository branch-role policy is incomplete")
    assert isinstance(release, str)
    assert isinstance(accepted, str)
    assert isinstance(candidate, str)
    return frozenset((release, accepted, candidate)), frozenset((release, accepted))


def remote_branches(root: Path, remote: str, expected: frozenset[str]) -> list[str]:
    """Return provider branches outside the declared persistent roles."""
    refs = output("git", "ls-remote", "--heads", remote, cwd=root).splitlines()
    return sorted(
        ref.removeprefix("refs/heads/")
        for line in refs
        if len(parts := line.split("\t", 1)) == 2
        for ref in [parts[1]]
        if ref.removeprefix("refs/heads/") not in expected
    )


def local_branches(root: Path, expected: frozenset[str]) -> list[str]:
    """Return local branches outside the declared persistent roles."""
    return sorted(
        branch
        for branch in output(
            "git", "for-each-ref", "refs/heads", "--format=%(refname:short)", cwd=root
        ).splitlines()
        if branch not in expected
    )


def local_branch_oids(root: Path, branches: frozenset[str]) -> dict[str, str]:
    """Return exact local persistent branch commits."""
    return {
        branch: output("git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}", cwd=root)
        for branch in sorted(branches)
    }


def remote_branch_oids(root: Path, remote: str, branches: frozenset[str]) -> dict[str, str]:
    """Return exact remote persistent branch commits."""
    refs = output(
        "git",
        "ls-remote",
        "--heads",
        remote,
        *(f"refs/heads/{branch}" for branch in sorted(branches)),
        cwd=root,
    ).splitlines()
    observed = {
        ref.removeprefix("refs/heads/"): oid
        for line in refs
        if len(parts := line.split("\t", 1)) == 2
        for oid, ref in [parts]
    }
    if set(observed) != branches:
        raise RuntimeError(f"{remote} does not expose the declared persistent refs")
    return observed


def exact_branch_parity(local: dict[str, str], *peers: dict[str, str]) -> bool:
    """Return whether every persistent ref names one product commit."""
    return bool(local) and len(set(local.values())) == 1 and all(peer == local for peer in peers)


def _tag_names(repository: Path) -> list[str]:
    """Return local SemVer tag names."""
    return output("git", "tag", "--list", "v[0-9]*", cwd=repository).splitlines()


def _tag_evidence(repository: Path, tags: list[str], anchor: Path) -> dict[str, dict[str, object]]:
    """Describe and verify exact annotated tag objects in one repository."""
    evidence: dict[str, dict[str, object]] = {}
    for tag in tags:
        reference = f"refs/tags/{tag}"
        annotated = output("git", "cat-file", "-t", reference, cwd=repository) == "tag"
        signature = (
            annotated
            and command(
                "git",
                "-c",
                "gpg.format=ssh",
                "-c",
                "gpg.ssh.program=ssh-keygen",
                "-c",
                f"gpg.ssh.allowedSignersFile={anchor.resolve()}",
                "verify-tag",
                tag,
                cwd=repository,
                check=False,
            ).returncode
            == 0
        )
        evidence[tag] = {
            "tag_object_oid": output("git", "rev-parse", reference, cwd=repository),
            "commit_oid": output("git", "rev-parse", f"{reference}^{{commit}}", cwd=repository),
            "tree_oid": output("git", "rev-parse", f"{reference}^{{tree}}", cwd=repository),
            "annotated": annotated,
            "signature_verified": signature,
        }
    return evidence


def local_release_evidence(root: Path, anchor: Path) -> dict[str, dict[str, object]]:
    """Return local release-tag object evidence."""
    return _tag_evidence(root, _tag_names(root), anchor)


def provider_release_evidence(
    root: Path, remote: str, anchor: Path
) -> dict[str, dict[str, object]]:
    """Fetch and verify one peer's release-tag objects in isolation."""
    remote_url = output("git", "config", "--local", "--get", f"remote.{remote}.url", cwd=root)
    with tempfile.TemporaryDirectory(prefix=f"{product_identity.PRODUCT_SLUG}-parity-") as name:
        repository = Path(name) / "repository.git"
        command("git", "init", "--quiet", "--bare", str(repository), cwd=root)
        command(
            "git",
            "-C",
            str(repository),
            "fetch",
            "--quiet",
            "--force",
            "--no-tags",
            remote_url,
            "+refs/tags/*:refs/tags/*",
            cwd=root,
        )
        return _tag_evidence(repository, _tag_names(repository), anchor)


def exact_tag_parity(
    local: dict[str, dict[str, object]],
    *peers: dict[str, dict[str, object]],
) -> bool:
    """Return whether all peers expose the same verified annotated tag objects."""
    return (
        bool(local)
        and all(peer == local for peer in peers)
        and all(
            tag["annotated"] is True and tag["signature_verified"] is True for tag in local.values()
        )
    )


def _verify_product_commit(root: Path, commit: str, anchor: Path, email: str) -> bool:
    """Verify the shared commit identity once at the local authority."""
    identities = output("git", "show", "-s", "--format=%ae%n%ce", commit, cwd=root).splitlines()
    return (
        identities == [email, email]
        and command(
            "git",
            "-c",
            "gpg.format=ssh",
            "-c",
            "gpg.ssh.program=ssh-keygen",
            "-c",
            f"gpg.ssh.allowedSignersFile={anchor.resolve()}",
            "verify-commit",
            commit,
            cwd=root,
            check=False,
        ).returncode
        == 0
    )


def audit(
    *,
    root: Path,
    commit_anchor: Path,
    author_email: str,
    tag_anchor: Path,
    peers: tuple[str, ...] = (),
) -> dict[str, object]:
    """Verify local objects and only the explicitly selected independent peers."""
    configured = set(output("git", "remote", cwd=root).splitlines())
    if len(set(peers)) != len(peers) or "local" in peers or not set(peers) <= configured:
        raise RuntimeError("peer selection must name distinct configured remotes")
    local_roles, remote_roles = branches_for_audit(root)
    local_refs = local_branch_oids(root, remote_roles)
    peer_refs = {peer: remote_branch_oids(root, peer, remote_roles) for peer in peers}
    branches_equal = exact_branch_parity(local_refs, *peer_refs.values())
    local_tags = local_release_evidence(root, tag_anchor)
    peer_tags = {peer: provider_release_evidence(root, peer, tag_anchor) for peer in peers}
    tags_equal = exact_tag_parity(local_tags, *peer_tags.values())
    unexpected = {"local": local_branches(root, local_roles)} | {
        peer: remote_branches(root, peer, remote_roles) for peer in peers
    }
    housekeeping = {
        "unexpected_branches": unexpected,
        "worktrees": output("git", "worktree", "list", "--porcelain", cwd=root).splitlines(),
    }
    commit_verified = all(
        _verify_product_commit(root, commit, commit_anchor, author_email)
        for commit in set(local_refs.values())
    )
    result: dict[str, object] = {
        "branches": {"local": local_refs, **peer_refs},
        "branch_object_parity": branches_equal,
        "product_commit_verified": commit_verified,
        "tags": {"local": local_tags, **peer_tags},
        "tag_object_parity": tags_equal,
        "housekeeping": housekeeping,
    }
    result["ok"] = (
        branches_equal and commit_verified and tags_equal and not any(unexpected.values())
    )
    return result


def _command(
    *,
    commit_anchor: Path,
    author_email: str,
    tag_anchor: Path,
    peers: Annotated[tuple[str, ...], Parameter(name="--peer", consume_multiple=False)] = (),
    root: Path | None = None,
    as_json: Annotated[bool, Parameter(name="--json", negative=False)] = False,
) -> None:
    """Collect live exact-object parity from explicit product trust inputs."""
    root = (root or Path.cwd()).resolve()
    for path in (commit_anchor, tag_anchor):
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"required publication trust input is unavailable: {path}")
    try:
        evidence = audit(
            root=root,
            commit_anchor=commit_anchor,
            author_email=author_email,
            tag_anchor=tag_anchor,
            peers=peers,
        )
    except RuntimeError as error:
        raise SystemExit(f"ERROR: {error}") from error
    if as_json:
        print(json.dumps(evidence, sort_keys=True))
    else:
        print(f"commit objects: {'identical' if evidence['branch_object_parity'] else 'different'}")
        print(f"tag objects: {'identical' if evidence['tag_object_parity'] else 'different'}")
        print(f"housekeeping: {'OK' if evidence['ok'] else 'FAILED'}")
    if not evidence["ok"]:
        raise SystemExit(1)


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run Forge parity audit through the repository's single parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
