"""Contracts for exact product-object parity across optional Forge peers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

import pytest

from tools.forge import audit
from tools.forge.audit import branches_for_audit
from tools.forge.audit import exact_branch_parity
from tools.forge.audit import exact_tag_parity
from tools.forge.audit import remote_branch_oids
from tools.git_environment import isolated_config_environment


class ForgeFixture(TypedDict):
    """One signed local source and two independent bare peers."""

    source: Path
    gitlab: Path
    github: Path
    anchor: Path
    email: str


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run Git with isolated user configuration and reject a nonzero exit."""
    result = subprocess.run(
        args,
        cwd=cwd,
        env=isolated_config_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


@pytest.fixture
def forge_fixture(tmp_path: Path) -> ForgeFixture:
    """Create a portable signed source and two empty peer repositories."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("OpenSSH signing is unavailable")
    source = tmp_path / "source"
    gitlab = tmp_path / "gitlab.git"
    github = tmp_path / "github.git"
    key = tmp_path / "signing"
    anchor = tmp_path / "allowed-signers"
    email = "product@example.test"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), cwd=tmp_path)
    public = " ".join(key.with_suffix(".pub").read_text(encoding="ascii").split()[:2])
    anchor.write_text(f'{email} namespaces="git" {public}\n', encoding="ascii")
    for remote in (gitlab, github):
        run("git", "init", "-q", "--bare", str(remote), cwd=tmp_path)
    run("git", "init", "-q", "-b", "main", str(source), cwd=tmp_path)
    for key_name, value in (
        ("core.hooksPath", os.devnull),
        ("user.name", "Product Publisher"),
        ("user.email", email),
        ("user.useConfigOnly", "true"),
        ("gpg.format", "ssh"),
        ("gpg.ssh.program", "ssh-keygen"),
        ("user.signingkey", str(key)),
    ):
        run("git", "config", key_name, value, cwd=source)
    (source / "README.md").write_text("one\n", encoding="utf-8")
    run("git", "add", "README.md", cwd=source)
    run("git", "commit", "-qS", "-m", "test: README.md", cwd=source)
    run("git", "branch", "dev", cwd=source)
    run("git", "remote", "add", "origin", str(gitlab), cwd=source)
    run("git", "remote", "add", "github", str(github), cwd=source)
    return {
        "source": source,
        "gitlab": gitlab,
        "github": github,
        "anchor": anchor,
        "email": email,
    }


def _tags(oid: str) -> dict[str, dict[str, object]]:
    return {
        "v1.0.0": {
            "tag_object_oid": oid,
            "commit_oid": "commit",
            "tree_oid": "tree",
            "annotated": True,
            "signature_verified": True,
        }
    }


class TestForgeAuditContracts:
    """Require exact Git object equality rather than historical approximation."""

    def test_branch_parity_requires_one_oid_for_local_and_both_peers(self) -> None:
        common = {"main": "product", "dev": "product"}
        assert exact_branch_parity(common, common, common)
        assert not exact_branch_parity(common, common, {"main": "other", "dev": "other"})

    def test_tag_parity_requires_the_same_verified_annotated_object(self) -> None:
        assert exact_tag_parity(_tags("tag"), _tags("tag"), _tags("tag"))
        assert not exact_tag_parity(_tags("tag"), _tags("other"), _tags("tag"))
        unsigned = _tags("tag")
        unsigned["v1.0.0"]["signature_verified"] = False
        assert not exact_tag_parity(unsigned, unsigned, unsigned)

    def test_branch_inventory_follows_declared_repository_roles(self, tmp_path: Path) -> None:
        policy = tmp_path / "workspace.toml"
        policy.write_text(
            """\
[branch_roles]
release_branch = "stable"
accepted_branch = "integration"
candidate_branch = "candidate/integration"
work_branch_prefix = "work/"
proposal_branch_prefix = "proposal/"
""",
            encoding="utf-8",
        )

        assert branches_for_audit(policy) == (
            frozenset({"stable", "integration", "candidate/integration"}),
            frozenset({"stable", "integration"}),
        )

    def test_remote_branch_reader_requires_main_and_dev(self, tmp_path: Path) -> None:
        remote = tmp_path / "remote.git"
        source = tmp_path / "source"
        run("git", "init", "--bare", str(remote), cwd=tmp_path)
        run("git", "init", "-b", "main", str(source), cwd=tmp_path)
        run("git", "config", "user.name", "Test", cwd=source)
        run("git", "config", "user.email", "test@example.test", cwd=source)
        (source / "README.md").write_text("test\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=source)
        run("git", "commit", "-m", "test", cwd=source)
        run("git", "remote", "add", "peer", str(remote), cwd=source)
        run("git", "push", "peer", "main", cwd=source)

        with pytest.raises(RuntimeError, match="declared persistent refs"):
            remote_branch_oids(source, "peer", frozenset(("main", "dev")))

        run("git", "push", "peer", "main:dev", cwd=source)
        oid = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
        assert remote_branch_oids(source, "peer", frozenset(("main", "dev"))) == {
            "main": oid,
            "dev": oid,
        }


@pytest.mark.parametrize("peers", [(), ("origin",), ("github",), ("origin", "github")])
def test_selected_peers_are_optional_and_object_preserving(forge_fixture: ForgeFixture, peers):
    source = forge_fixture["source"]
    policy = source / ".ethos" / "workspace.toml"
    policy.parent.mkdir()
    policy.write_text(
        '[branch_roles]\nrelease_branch="main"\naccepted_branch="dev"\ncandidate_branch="candidate/dev"\n'
    )
    run("git", "tag", "-s", "v1.0.0", "-m", "Release", cwd=source)
    for remote in ("origin", "github"):
        if remote in peers:
            run("git", "push", remote, "main", "dev", "--tags", cwd=source)
        else:
            run("git", "remote", "remove", remote, cwd=source)
    refs_before = run("git", "show-ref", cwd=source).stdout

    report = audit.audit(
        root=source,
        commit_anchor=forge_fixture["anchor"],
        author_email=forge_fixture["email"],
        tag_anchor=forge_fixture["anchor"],
        peers=peers,
    )

    assert report["ok"]
    branches, tags = report["branches"], report["tags"]
    assert isinstance(branches, dict)
    assert isinstance(tags, dict)
    assert set(branches) == {"local", *peers}
    assert set(tags) == {"local", *peers}
    assert refs_before == run("git", "show-ref", cwd=source).stdout


def test_audit_uses_declared_roles_instead_of_main_dev(forge_fixture: ForgeFixture):
    source = forge_fixture["source"]
    run("git", "branch", "-m", "main", "stable", cwd=source)
    run("git", "branch", "-m", "dev", "integration", cwd=source)
    policy = source / ".ethos" / "workspace.toml"
    policy.parent.mkdir()
    policy.write_text(
        '[branch_roles]\nrelease_branch="stable"\naccepted_branch="integration"\ncandidate_branch="candidate/integration"\n'
    )
    run("git", "tag", "-s", "v1.0.0", "-m", "Release", cwd=source)
    report = audit.audit(
        root=source,
        commit_anchor=forge_fixture["anchor"],
        author_email=forge_fixture["email"],
        tag_anchor=forge_fixture["anchor"],
    )
    assert report["ok"]
    branches = report["branches"]
    assert isinstance(branches, dict)
    assert set(branches["local"]) == {"stable", "integration"}


@pytest.mark.parametrize("peers", [("origin", "origin"), ("absent",), ("local",)])
def test_invalid_peer_selection_fails_before_observation(
    forge_fixture: ForgeFixture, peers, mocker
):
    remote = mocker.patch.object(audit, "remote_branch_oids")
    with pytest.raises(RuntimeError, match="peer"):
        audit.audit(
            root=forge_fixture["source"],
            commit_anchor=forge_fixture["anchor"],
            author_email=forge_fixture["email"],
            tag_anchor=forge_fixture["anchor"],
            peers=peers,
        )
    remote.assert_not_called()


@pytest.mark.parametrize(
    ("local", "peer"), [({}, {}), ({"stable": "x"}, {}), ({"stable": "x"}, {"other": "x"})]
)
def test_branch_parity_rejects_missing_or_different_roles(local, peer):
    assert not exact_branch_parity(local, peer)


@pytest.mark.parametrize(
    "text",
    [
        "[broken",
        "",
        "[branch_roles]\n",
        '[branch_roles]\nrelease_branch=""\naccepted_branch="dev"\ncandidate_branch="candidate/dev"',
    ],
)
def test_branch_role_policy_must_be_complete(text, tmp_path):
    policy = tmp_path / "workspace.toml"
    policy.write_text(text)
    with pytest.raises(RuntimeError, match="policy"):
        branches_for_audit(policy)


def test_missing_policy_is_reported_without_guessing(tmp_path):
    with pytest.raises(RuntimeError, match="policy"):
        branches_for_audit(tmp_path / "missing")


def test_subprocess_failure_is_bounded_and_checked(tmp_path):
    with pytest.raises(RuntimeError):
        audit.command("git", "rev-parse", "--verify", "missing", cwd=tmp_path)
    assert (
        audit.command(
            "git", "rev-parse", "--verify", "missing", cwd=tmp_path, check=False
        ).returncode
        != 0
    )


def test_release_audit_detects_untrusted_tags_identity_and_residual_branches(forge_fixture):
    source = forge_fixture["source"]
    anchor = forge_fixture["anchor"]
    commit = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    assert audit._verify_product_commit(source, commit, anchor, forge_fixture["email"])
    assert not audit._verify_product_commit(source, commit, anchor, "other@example.test")
    run("git", "tag", "v1.0.0", cwd=source)
    tags = audit.local_release_evidence(source, anchor)
    assert not tags["v1.0.0"]["annotated"]
    assert not exact_tag_parity(tags)
    assert not exact_tag_parity({})
    run("git", "branch", "proposal/owned", cwd=source)
    run("git", "push", "origin", "main", "dev", "proposal/owned", cwd=source)
    assert audit.local_branches(source, frozenset(("main", "dev"))) == ["proposal/owned"]
    assert audit.remote_branches(source, "origin", frozenset(("main", "dev"))) == ["proposal/owned"]


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_exact_selected_peer_scope(as_json, forge_fixture, mocker, capsys):
    expected = {"ok": True, "branch_object_parity": True, "tag_object_parity": True}
    collect = mocker.patch.object(audit, "audit", return_value=expected)
    args = [
        "--commit-anchor",
        str(forge_fixture["anchor"]),
        "--tag-anchor",
        str(forge_fixture["anchor"]),
        "--author-email",
        forge_fixture["email"],
        "--root",
        str(forge_fixture["source"]),
        "--peer",
        "origin",
        "--peer",
        "github",
    ]
    if as_json:
        args.append("--json")
    audit.main(tuple(args))
    assert collect.call_args.kwargs["peers"] == ("origin", "github")
    output = capsys.readouterr().out
    if as_json:
        assert '"ok": true' in output
    else:
        assert "housekeeping: OK" in output


@pytest.mark.parametrize("failure", ["anchor", "observation", "parity"])
def test_cli_failure_is_nonzero(failure, forge_fixture, mocker):
    anchor = forge_fixture["anchor"]
    if failure == "anchor":
        anchor = anchor.with_name("absent")
    elif failure == "observation":
        mocker.patch.object(audit, "audit", side_effect=RuntimeError("unreachable peer"))
    else:
        mocker.patch.object(
            audit,
            "audit",
            return_value={"ok": False, "branch_object_parity": False, "tag_object_parity": False},
        )
    with pytest.raises(SystemExit):
        audit._command(
            commit_anchor=anchor,
            tag_anchor=anchor,
            author_email=forge_fixture["email"],
            root=forge_fixture["source"],
        )
