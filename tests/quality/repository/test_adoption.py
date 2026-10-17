"""Repository adoption and independent publication declarations."""

from __future__ import annotations

import tomllib

from tests.quality.fixtures import ROOT


def test_openspec_material_scope_covers_every_repository_carrier() -> None:
    profile = tomllib.loads((ROOT / ".ethos/profile.toml").read_text(encoding="utf-8"))

    assert profile["openspec"]["material_paths"] == ["**"]


def test_publication_topology_has_only_declared_independent_peers() -> None:
    publication = tomllib.loads((ROOT / ".ethos/release.toml").read_text(encoding="utf-8"))[
        "publication"
    ]

    assert set(publication) == {
        "local_verification_command",
        "local_installation_command",
        "peers",
    }
    assert publication["peers"] == [
        {
            "id": "gitlab",
            "provider": "gitlab",
            "role": "organization_collaboration",
            "git_remote": "gitlab",
            "capabilities": ["repository", "ci_cd", "publication"],
            "ci_surface": ".gitlab-ci.yml",
        },
        {
            "id": "github",
            "provider": "github",
            "role": "public_distribution",
            "git_remote": "github",
            "capabilities": ["repository", "ci_cd", "publication"],
            "ci_surface": ".github/workflows/verify.yml",
        },
    ]


def test_release_binding_delegates_role_and_signature_policy_to_ethos() -> None:
    workspace = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))
    release = tomllib.loads((ROOT / ".ethos/release.toml").read_text(encoding="utf-8"))

    assert set(workspace) == {"commit_policy"}
    assert release["release"] == {"mirror": "accepted_ff"}


def test_commit_policy_restricts_scopes_without_redeclaring_grammar() -> None:
    workspace = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))
    assert set(workspace["commit_policy"]) == {"scopes"}
    scopes = workspace["commit_policy"]["scopes"]
    assert scopes
    assert len(scopes) == len(set(scopes))
