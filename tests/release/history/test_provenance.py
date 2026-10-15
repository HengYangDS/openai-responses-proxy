"""Bind release provenance to the selected checkout, not the global tag set."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.git_environment import isolated_config_environment
from tools.release import metadata


@pytest.fixture
def release_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., str]:
    """Retain descendant and sibling tags while selecting the first release."""
    environment = isolated_config_environment()

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Release Fixture",
                "-c",
                "user.email=release@example.test",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                *arguments,
            ],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
            text=True,
        ).strip()

    git("init", "--initial-branch=main")
    git("commit", "--allow-empty", "-m", "first release")
    first = git("rev-parse", "HEAD")
    git("tag", "-a", "v1.0.0", "-m", "first release")
    git("commit", "--allow-empty", "-m", "future release")
    git("tag", "-a", "v2.0.0", "-m", "future release")
    git("checkout", "-b", "sibling", first)
    git("commit", "--allow-empty", "-m", "sibling release")
    git("tag", "-a", "v9.0.0", "-m", "sibling release")
    git("tag", "-a", "v0.9.0", "-m", "retained historical identity")
    git("checkout", "--detach", first)
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(metadata, "ROOT", tmp_path)
    return git


def test_historical_checkout_ignores_descendant_and_sibling_tags(release_history) -> None:
    """A later publication must not invalidate a retained historical checkout."""
    assert release_history("tag", "--list") == "v0.9.0\nv1.0.0\nv2.0.0\nv9.0.0"
    assert metadata.known_release_versions() == ["1.0.0", "0.9.0"]
    releases = [("1.0.0", "2026-01-01"), ("0.9.0", "2025-12-31")]
    metadata.check_changelog_provenance(releases)
    metadata.check_active_release_train("1.0.0", releases)


def test_historical_checkout_still_rejects_a_missing_product_tag(release_history) -> None:
    """History scoping does not excuse a dated release whose tag is absent."""
    release_history("tag", "--delete", "v1.0.0")
    with pytest.raises(
        ValueError, match=re.escape("released CHANGELOG heading has no product tag: 1.0.0")
    ):
        metadata.check_changelog_provenance([("1.0.0", "2026-01-01"), ("0.9.0", "2025-12-31")])


def test_release_preparation_rejects_a_retained_nonancestor_tag(
    release_history, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preparation cannot reuse an existing tag outside the selected history."""
    assert "v2.0.0" in release_history("tag", "--list")
    monkeypatch.setattr(metadata, "read_version", lambda: "2.0.0")
    monkeypatch.setattr(metadata, "check_python_metadata", lambda: None)
    monkeypatch.setattr(
        metadata,
        "changelog_releases",
        lambda _path: [
            ("2.0.0", "2026-01-02"),
            ("1.0.0", "2026-01-01"),
            ("0.9.0", "2025-12-31"),
        ],
    )
    with pytest.raises(SystemExit, match=re.escape("release tag v2.0.0 already exists")):
        metadata.main(("--prepare-release",))
