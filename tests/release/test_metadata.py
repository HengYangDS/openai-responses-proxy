"""Regression tests for release-history provenance enforcement."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path

import pytest

from tools.release import identity
from tools.release import metadata

ROOT = Path(__file__).resolve().parents[2]
METADATA_MODULE = "tools.release.metadata"
TAG_REFRESH = "git fetch --tags --force --prune --prune-tags origin"
APT_INSTALL = "apt-get install -qq -y --no-install-recommends"
GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


def _run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def ci_block(source: str, job: str, next_job: str | None = None) -> str:
    """Return one exact top-level CI job or template block."""
    start = source.index(job)
    end = source.index(next_job, start) if next_job else source.find("\n\n", start)
    return source[start : end if end >= 0 else None]


def require_tokens(source: str, tokens: tuple[str, ...], context: str) -> None:
    """Require every literal contract token in one source surface."""
    missing = [token for token in tokens if token not in source]
    assert not missing, f"{context} is missing {missing[0] if missing else ''}"


def changelog_document(sections: str) -> str:
    """Return one minimal Keep a Changelog document."""
    return (
        "# Changelog\n\n"
        "This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) "
        "and [Semantic Versioning](https://semver.org/).\n\n"
        f"{sections}"
    )


def test_release_identity_has_one_strict_semver_and_tag_contract() -> None:
    """Keep release versions and annotated tag names on one exact grammar."""
    for version in ("0.0.0", "1.2.3", "20.56.300"):
        assert identity.is_version(version)
        tag = f"v{version}"
        assert identity.is_tag(tag)
        assert identity.version_from_tag(tag) == version
    for invalid in ("01.2.3", "1.02.3", "1.2", "1.2.3-rc.1", "v1.2.3"):
        assert not identity.is_version(invalid)
    for invalid in ("1.2.3", "v01.2.3", "v1.2", "v1.2.3-rc.1"):
        assert not identity.is_tag(invalid)
        with pytest.raises(ValueError, match=re.escape("exact vMAJOR.MINOR.PATCH")):
            identity.version_from_tag(invalid)


def test_product_release_history_is_provider_neutral(*, mocker) -> None:
    """Validate the one local release history without a Forge semantic input."""
    releases = [
        ("1.0.3", "2026-07-03"),
        ("1.0.2", "2026-07-02"),
        ("1.0.1", "2026-07-01"),
    ]
    known = mocker.patch.object(metadata, "known_release_versions", return_value=["1.0.2", "1.0.1"])
    metadata.check_changelog_provenance(releases, pending_version="1.0.3")
    with pytest.raises(ValueError, match="has no product tag"):
        metadata.check_changelog_provenance(releases)
    known.return_value = ["1.0.2"]
    with pytest.raises(ValueError, match="must appear once"):
        metadata.check_changelog_provenance([("1.0.3", "2026-07-03"), ("1.0.1", "2026-07-01")])


def test_prepare_release_rejects_only_future_dates() -> None:
    """Keep prepared metadata stable across days while rejecting impossible chronology."""
    current = date(2026, 7, 27)
    metadata.check_pending_release_date("1.2.3", [("1.2.3", "2026-07-27")], today=current)
    metadata.check_pending_release_date("1.2.3", [("1.2.3", "2026-07-26")], today=current)
    with pytest.raises(ValueError, match="future UTC date"):
        metadata.check_pending_release_date("1.2.3", [("1.2.3", "2026-07-28")], today=current)


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3", "01.2.3", "1.2.3-rc.1", ""])
def test_version_file_uses_the_product_grammar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
    monkeypatch.setattr(metadata, "ROOT", tmp_path)
    if identity.is_version(version):
        assert metadata.read_version() == version
    else:
        with pytest.raises(ValueError, match="release SemVer"):
            metadata.read_version()


@pytest.mark.parametrize(
    ("before", "after", "diagnostic"),
    [
        ('requires-python = ">=3.12"', 'requires-python = ">=3.13"', "require Python"),
        ('dynamic = ["version"]', "dynamic = []", "sole version owner"),
        ('path = "VERSION"', 'path = "version.txt"', "read package version"),
        ('distribution = "native-executable"', 'distribution = "wheel"', "native executable"),
    ],
)
def test_package_metadata_keeps_version_and_distribution_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    after: str,
    diagnostic: str,
) -> None:
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    target = tmp_path / "pyproject.toml"
    target.write_text(source, encoding="utf-8")
    monkeypatch.setattr(metadata, "ROOT", tmp_path)
    metadata.check_python_metadata()
    assert source.count(before) == 1
    target.write_text(source.replace(before, after), encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        metadata.check_python_metadata()


@pytest.mark.parametrize(
    ("known", "headings", "current", "prepared", "diagnostic"),
    [
        (["1.0.0"], ["1.0.0"], "1.0.0", False, None),
        (["1.0.0"], [], "1.0.0", False, "lacks dated release heading"),
        (["1.0.0"], ["1.0.1", "1.0.0"], "1.0.1", True, None),
        (["1.0.0"], ["1.0.1", "1.0.0"], "1.0.1", False, None),
        ([], ["1.0.0"], "1.0.0", False, None),
        (["2.0.0"], ["1.0.0"], "1.0.0", False, "exists before its Git tag"),
        ([], ["2.0.0", "1.0.0"], "1.0.0", False, "exists before its Git tag"),
        (["1.0.0"], ["1.0.0"], "1.0.1", False, None),
        (["1.0.1"], ["1.0.1"], "1.0.0", False, "must be newer"),
        ([], [], "1.0.0", False, "cannot identify"),
    ],
)
def test_release_train_admission_uses_exact_local_chronology(
    monkeypatch: pytest.MonkeyPatch,
    known: list[str],
    headings: list[str],
    current: str,
    prepared: bool,
    diagnostic: str | None,
) -> None:
    monkeypatch.setattr(metadata, "known_release_versions", lambda: known)
    releases = [(version, "2026-07-01") for version in headings]
    if diagnostic is None:
        metadata.check_active_release_train(current, releases, pending_release=prepared)
    else:
        with pytest.raises(ValueError, match=diagnostic):
            metadata.check_active_release_train(current, releases, pending_release=prepared)


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("", "must start"),
        ("# Changelog\n\n## [Unreleased]\n", "Keep a Changelog"),
        (changelog_document("## [1.0.0] - 2026-07-01\n"), "must start"),
        (changelog_document("## [Unreleased]\n## [Unreleased]\n"), "exactly one"),
        (changelog_document("## [Unreleased]\n## [1.0.0]\n"), "must be dated"),
        (
            changelog_document(
                "## [Unreleased]\n\n## [1.0.0] - 2026-07-01\n\n### Fixed\n\n- Fix.\n\n"
                "## [2.0.0] - 2026-07-02\n\n### Fixed\n\n- Fix.\n"
            ),
            "descending",
        ),
        (
            changelog_document(
                "## [Unreleased]\n\n## [1.0.0] - 2026-07-01\n\n### Quality\n\n- Improve.\n"
            ),
            "canonical category",
        ),
        (
            changelog_document(
                "## [Unreleased]\n\n## [1.0.0] - 2026-07-01\n\n### Fixed\n\n- One.\n\n"
                "### Fixed\n\n- Two.\n"
            ),
            "duplicate category",
        ),
        (
            changelog_document(
                "## [Unreleased]\n\n## [1.0.0] - 2026-02-30\n\n### Fixed\n\n- Fix.\n"
            ),
            "valid date",
        ),
        (
            changelog_document("## [Unreleased]\n\n## [1.0.0] - 2026-07-01\n\n### Fixed\n"),
            "change item",
        ),
    ],
)
def test_changelog_requires_ordered_dated_release_sections(
    tmp_path: Path, source: str, diagnostic: str
) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        metadata.changelog_releases(path)


def test_changelog_accepts_unreleased_only_and_canonical_categories(tmp_path: Path) -> None:
    initial = tmp_path / "initial.md"
    initial.write_text(changelog_document("## [Unreleased]\n"), encoding="utf-8")
    assert metadata.changelog_releases(initial) == []

    released = tmp_path / "released.md"
    released.write_text(
        changelog_document(
            "## [Unreleased]\n\n### Changed\n\n- Pending.\n\n"
            "## [1.0.0] - 2026-07-01\n\n### Added\n\n- Initial release.\n"
        ),
        encoding="utf-8",
    )
    assert metadata.changelog_releases(released) == [("1.0.0", "2026-07-01")]


def test_exact_release_tag_contract(*, mocker) -> None:
    """Reject lightweight, misnamed, nested, and wrong-target release tags."""
    cases = (
        (
            lambda *args: "commit" if args[:2] == ("cat-file", "-t") else "v1.2.3",
            "annotated tag object",
            "a lightweight release tag",
        ),
        (
            lambda *args: {
                ("cat-file", "-t", "refs/tags/v1.2.3"): "tag",
                ("cat-file", "tag", "refs/tags/v1.2.3"): (
                    "object same-commit\ntype commit\ntag v9.9.9\n\nmessage"
                ),
            }[args],
            "embeds tag name",
            "an annotated tag with the wrong embedded name",
        ),
        (
            lambda *args: {
                ("cat-file", "-t", "refs/tags/v1.2.3"): "tag",
                ("cat-file", "tag", "refs/tags/v1.2.3"): (
                    "object inner-tag\ntype tag\ntag v1.2.3\n\nmessage"
                ),
            }[args],
            "directly name a commit",
            "a nested annotated release tag",
        ),
        (
            lambda *args: {
                ("cat-file", "-t", "refs/tags/v1.2.3"): "tag",
                ("cat-file", "tag", "refs/tags/v1.2.3"): (
                    "object tagged-commit\ntype commit\ntag v1.2.3\n\nmessage"
                ),
                ("rev-parse", "HEAD^{commit}"): "head-commit",
            }[args],
            "not HEAD commit",
            "an annotated tag that directly names the wrong commit",
        ),
    )
    git = mocker.patch.object(metadata, "_git")
    for git_observation, message, _description in cases:
        git.side_effect = git_observation
        with pytest.raises(ValueError, match=message):
            metadata.check_release_tag("v1.2.3", "1.2.3")
    git.side_effect = lambda *args: {
        ("cat-file", "-t", "refs/tags/v1.2.3"): "tag",
        (
            "cat-file",
            "tag",
            "refs/tags/v1.2.3",
        ): "object same-commit\ntype commit\ntag v1.2.3\n\nmessage",
        ("rev-parse", "HEAD^{commit}"): "same-commit",
    }[args]
    metadata.check_release_tag("v1.2.3", "1.2.3")


def test_release_metadata_command_has_no_forge_semantics() -> None:
    """Keep ordinary product validation independent from publication peers."""
    completed = _run(sys.executable, "-m", METADATA_MODULE)
    assert (completed).returncode == 0
    legacy = _run(sys.executable, "-m", METADATA_MODULE, "--provider", "gitlab")
    assert legacy.returncode != 0, "metadata retained a Forge-specific compatibility flag"


def test_canonical_git_environment_is_isolated_from_personal_config() -> None:
    """Keep the shared Git subprocess environment free of personal config."""
    git_environment = (ROOT / "tools" / "git_environment.py").read_text(encoding="utf-8")
    require_tokens(
        git_environment,
        ('"GIT_CONFIG_GLOBAL": os.devnull', '"GIT_CONFIG_NOSYSTEM": "1"'),
        "canonical Git subprocess environment",
    )


def test_prune_tags_removes_deleted_remote_tag() -> None:
    """Reproduce the reused-runner stale-tag failure without network access."""
    with tempfile.TemporaryDirectory(prefix="openai-responses-proxy-prune-tags-") as temp:
        temp_root = Path(temp)
        remote = temp_root / "remote.git"
        publisher = temp_root / "publisher"
        reused_runner = temp_root / "reused-runner"

        for args in (
            ("git", "init", "--bare", str(remote)),
            ("git", "init", str(publisher)),
        ):
            assert (_run(*args, cwd=temp_root)).returncode == 0
        for args in (
            ("git", "config", "user.name", "Release Test"),
            ("git", "config", "user.email", "release@example.test"),
            ("git", "config", "user.useConfigOnly", "true"),
        ):
            assert (_run(*args, cwd=publisher)).returncode == 0
        (publisher / "README.md").write_text("release metadata fixture\n", encoding="utf-8")
        for args in (
            ("git", "add", "README.md"),
            ("git", "commit", "-m", "fixture"),
            ("git", "branch", "-M", "main"),
            ("git", "remote", "add", "origin", str(remote)),
            ("git", "push", "origin", "main"),
            ("git", "tag", "v9.9.9"),
            ("git", "push", "origin", "refs/tags/v9.9.9"),
        ):
            assert (_run(*args, cwd=publisher)).returncode == 0
        assert (
            _run("git", "clone", str(remote), str(reused_runner), cwd=temp_root)
        ).returncode == 0
        assert (_run("git", "push", "origin", ":refs/tags/v9.9.9", cwd=publisher)).returncode == 0
        assert (
            _run("git", "rev-parse", "--verify", "refs/tags/v9.9.9", cwd=reused_runner).returncode
            == 0
        ), "fixture did not retain the stale local tag"
        completed = _run(
            "git",
            "fetch",
            "--tags",
            "--force",
            "--prune",
            "--prune-tags",
            "origin",
            cwd=reused_runner,
        )
        assert (completed).returncode == 0
        assert (
            _run("git", "rev-parse", "--verify", "refs/tags/v9.9.9", cwd=reused_runner).returncode
            != 0
        ), "tag-pruning fetch retained a tag deleted from origin"


def test_github_tag_metadata_fetches_complete_provider_tags() -> None:
    """Require the tag proof to observe the complete provider tag namespace."""
    workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    start = workflow.index("\n  tag-metadata:")
    end = workflow.index("\n  python-quality:", start)
    checkout = workflow[start:end].split("- name: Fetch the exact", 1)[0]
    require_tokens(checkout, ("fetch-depth: 0", "fetch-tags: true"), "GitHub tag checkout")


def test_native_bundle_has_one_runtime_and_one_signer() -> None:
    """Build every platform once and sign only the complete assembled bundle."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    image = metadata["tool"]["openai-responses-proxy"]["linux-release-image"]
    github = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "@sha256:" in image, "Linux release runtime must be immutable"
    require_tokens(
        github,
        (
            "linux-release-image: ${{ steps.versions.outputs.linux-release-image }}",
            "container: ${{ needs.python-matrix.outputs.linux-release-image }}",
            "platform: macos-arm64",
            "platform: windows-x86_64",
            "python -m tools.release.artifact assemble",
            "--sign",
        ),
        "single native bundle builder",
    )
    assert github.count("python -m tools.release.artifact assemble") == 1, "bundle assembled twice"
    assert github.count("--sign") == 1, "bundle signed more than once"
    assert "nox -s release" not in gitlab, "GitLab independently rebuilds product assets"


def test_current_release_metadata_chronology(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise real repository chronology through the measured CLI owner."""
    source = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = metadata.read_version()
    tag_exists = _run("git", "rev-parse", "--verify", f"refs/tags/v{version}").returncode == 0
    prepared = f"## [{version}]" in source and not tag_exists
    metadata.main(("--prepare-release",) if prepared else ())
    assert f"metadata: {version} OK" in capsys.readouterr().out
    if not prepared:
        with pytest.raises((ValueError, SystemExit)):
            metadata.main(("--prepare-release",))
    tagged_version = metadata.known_release_versions()[0]
    release_section = re.compile(
        rf"(?ms)^## \[{re.escape(tagged_version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\n.*?(?=^## \[|\Z)"
    )
    incomplete = tmp_path / "CHANGELOG.md"
    incomplete.write_text(release_section.sub("", source, count=1), encoding="utf-8")
    with pytest.raises(ValueError, match="must appear once"):
        metadata.main(("--changelog", str(incomplete)))
    metadata.main(())
    assert f"metadata: {version} OK" in capsys.readouterr().out


@pytest.mark.parametrize(
    "defect",
    [
        "combined",
        "missing-heading",
        "not-first",
        "future",
        "invalid-train",
        "invalid-tag",
        "missing-tag-heading",
        "valid-prepared",
        "valid-tagged",
    ],
)
def test_cli_release_transition_admission(defect, mocker, capsys):
    mocker.patch.object(metadata, "read_version", return_value="1.2.3")
    mocker.patch.object(metadata, "check_python_metadata")
    releases = [("1.2.3", "2026-01-01"), ("1.2.2", "2025-12-31")]
    if defect in {"missing-heading", "missing-tag-heading"}:
        releases = releases[1:]
    elif defect == "not-first":
        releases = list(reversed(releases))
    elif defect == "future":
        releases[0] = ("1.2.3", "9999-01-01")
    mocker.patch.object(metadata, "changelog_releases", return_value=releases)
    mocker.patch.object(metadata, "known_release_versions", return_value=["1.2.2"])
    train = mocker.patch.object(metadata, "check_active_release_train")
    tag = mocker.patch.object(metadata, "check_release_tag")
    mocker.patch.object(metadata, "check_governance_contract")
    if defect == "invalid-train":
        train.side_effect = ValueError("invalid train")
    if defect == "invalid-tag":
        tag.side_effect = ValueError("invalid tag")
    args = ("--tag", "v1.2.3") if "tag" in defect else ("--prepare-release",)
    if defect == "combined":
        args = ("--prepare-release", "--tag", "v1.2.3")
    if defect.startswith("valid-"):
        metadata.main(args)
        assert "metadata: 1.2.3 OK" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit):
            metadata.main(args)


@pytest.mark.parametrize("releases", [[("1.2.3", "invalid")], []])
def test_pending_release_requires_an_actual_date(releases):
    with pytest.raises(ValueError, match="no valid release date"):
        metadata.check_pending_release_date("1.2.3", releases)


def test_duplicate_changelog_versions_cannot_claim_one_release(mocker):
    mocker.patch.object(metadata, "known_release_versions", return_value=[])
    with pytest.raises(ValueError, match="must not duplicate"):
        metadata.check_changelog_provenance([("1.2.3", "2026-01-01")] * 2)


def test_missing_tag_is_a_release_identity_error(mocker):
    mocker.patch.object(metadata, "_git", side_effect=subprocess.CalledProcessError(1, ["git"]))
    with pytest.raises(ValueError, match="does not exist"):
        metadata.check_release_tag("v1.2.3", "1.2.3")


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
