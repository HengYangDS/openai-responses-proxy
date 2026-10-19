"""Release governance contract observations, distinct from product metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.release import metadata


@pytest.mark.parametrize(
    ("carrier", "before", "after", "diagnostic"),
    [
        ("README.md", "# OpenAI Responses Proxy", "# Other", "formal Project Name"),
        (
            ".gitlab-ci.yml",
            "python -m tools.release.metadata",
            "unrelated",
            "release and governance checker",
        ),
        (".gitlab-ci.yml", "verify-release-tag:", "other-tag:", "exact trusted product tag"),
        (".gitlab-ci.yml", "", "\npublish-gitlab-release:\n", "must not rebuild"),
        (
            "docs/operations/forge-operations.md",
            "tools.forge.audit",
            "other",
            "read-only parity audit",
        ),
        (
            "docs/operations/forge-operations.md",
            "fast-forward or idempotent",
            "other",
            "exact object publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "ethos publish --ref refs/heads/dev",
            "ethos publish --ref refs/heads/other",
            "ETHOS branch publication",
        ),
        (
            ".ethos/release.toml",
            'branches = ["main", "dev"]',
            'branches = ["main", "dev", "next"]',
            "ETHOS branch publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "ethos publish --retire",
            "ethos publish",
            "ETHOS branch publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "`Admission`",
            "`Other`",
            "ETHOS branch publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "python -m tools.release.metadata --prepare-release",
            "python -m tools.release.metadata",
            "ETHOS tag publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "ethos land --release",
            "ethos land",
            "ETHOS tag publication",
        ),
        (
            ".ethos/release.toml",
            'tags = ["v*"]',
            'tags = ["release-*"]',
            "ETHOS tag publication",
        ),
        (
            "docs/operations/forge-operations.md",
            "",
            "\ncommit-author-name\n",
            "couple shared history",
        ),
    ],
)
def test_governance_reader_detects_contradicted_public_contract(
    carrier, before, after, diagnostic, mocker
):
    original = Path.read_text

    def read(path, *args, **kwargs):
        content = original(path, *args, **kwargs)
        if path == ROOT / carrier:
            return content.replace(before, after) if before else content + after
        return content

    mocker.patch.object(Path, "read_text", read)
    with pytest.raises(ValueError, match=diagnostic):
        metadata.check_governance_contract()


def test_governance_requires_real_repository_carriers(tmp_path, mocker):
    mocker.patch.object(metadata, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="missing governance documents"):
        metadata.check_governance_contract()


def test_forge_publication_has_no_implicit_actor_or_trust_source():
    source = (ROOT / "tools" / "release" / "tag_signature.py").read_text(encoding="utf-8")
    assert "packaging/release" not in source
    assert "/Users/" not in source
    assert "$HOME/.ssh" not in source
    assert "anchor" in source


def test_publication_has_importable_command_and_peer_owners():
    for owner in ("__main__", "verification", "github.publish", "gitlab.publish"):
        assert importlib.util.find_spec(f"tools.release.publication.{owner}") is not None
