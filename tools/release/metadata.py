"""Verify release identity, changelog provenance, and governance contracts."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

from openai_responses_proxy import product_identity
from tools.quality.python_matrix import supported_requirement
from tools.release import identity

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG_HEADING = re.compile(
    r"^## \[(?P<version>Unreleased|(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?$"
)
CHANGELOG_CATEGORIES = frozenset({"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"})
KEEP_A_CHANGELOG_URL = "https://keepachangelog.com/en/1.1.0/"
SEMVER_URL = "https://semver.org/"


def read_version() -> str:
    """Return the repository release version after strict SemVer validation."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not identity.is_version(version):
        raise ValueError(f"VERSION is not a release SemVer: {version!r}")
    return version


def check_python_metadata() -> None:
    """Keep Python support metadata aligned without duplicating release ownership."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata.get("project", {})
    requirement = supported_requirement(ROOT / ".python-versions")
    if project.get("requires-python") != requirement:
        raise ValueError(f"pyproject.toml must require Python {requirement} from .python-versions")
    if project.get("dynamic") != ["version"]:
        raise ValueError("pyproject.toml must keep VERSION as the sole version owner")
    hatch_version = metadata.get("tool", {}).get("hatch", {}).get("version", {})
    if hatch_version.get("path") != "VERSION":
        raise ValueError("pyproject.toml must read package version from VERSION")
    if metadata.get("tool", {}).get(product_identity.PRODUCT_SLUG, {}).get("distribution") != (
        "native-executable"
    ):
        raise ValueError("the product distribution must be the native executable")


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def known_release_versions() -> list[str]:
    """Return this checkout's product tags in descending SemVer order."""
    tags = _git("tag", "--list", "v[0-9]*", "--sort=-version:refname").splitlines()
    versions = [identity.version_from_tag(tag) for tag in tags if identity.is_tag(tag)]
    return sorted(versions, key=_version_key, reverse=True)


def changelog_releases(path: Path | None = None) -> list[tuple[str, str]]:
    """Return dated Changelog releases after validating section structure."""
    changelog = path or ROOT / "CHANGELOG.md"
    lines = changelog.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "# Changelog":
        raise ValueError("CHANGELOG.md must start with # Changelog")
    preamble = "\n".join(
        lines[: next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))]
    )
    if KEEP_A_CHANGELOG_URL not in preamble or SEMVER_URL not in preamble:
        raise ValueError(
            "CHANGELOG.md must identify Keep a Changelog 1.1.0 and Semantic Versioning"
        )

    headings: list[tuple[int, str, str | None]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.startswith("## "):
            continue
        match = CHANGELOG_HEADING.match(line)
        if match is None:
            raise ValueError(f"CHANGELOG.md release heading at line {line_number} must be dated")
        headings.append((line_number - 1, match.group("version"), match.group("date")))
    if not headings or headings[0][1] != "Unreleased":
        raise ValueError("CHANGELOG.md must start its release sections with ## [Unreleased]")
    if sum(1 for _, version, _ in headings if version == "Unreleased") != 1:
        raise ValueError("CHANGELOG.md must contain exactly one Unreleased section")
    if headings[0][2] is not None:
        raise ValueError("CHANGELOG.md Unreleased section must not be dated")
    released = [(version, item_date) for _, version, item_date in headings[1:]]
    if any(version == "Unreleased" or item_date is None for version, item_date in released):
        raise ValueError("released CHANGELOG headings must be dated and follow Unreleased")
    for version, item_date in released:
        try:
            date.fromisoformat(item_date or "")
        except ValueError as error:
            raise ValueError(
                f"released CHANGELOG heading for {version} must use a valid date"
            ) from error
    versions = [version for version, _ in released]
    if versions != sorted(versions, key=_version_key, reverse=True):
        raise ValueError("released CHANGELOG headings must be in descending SemVer order")
    for index, (start, version, _) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        _validate_changelog_section(lines[start + 1 : end], version)
    return [(version, date) for version, date in released if date is not None]


def _validate_changelog_section(lines: list[str], version: str) -> None:
    categories: set[str] = set()
    item_count = 0
    for line in lines:
        if line.startswith("### "):
            category = line.removeprefix("### ")
            if category not in CHANGELOG_CATEGORIES:
                raise ValueError(
                    f"CHANGELOG.md section {version} uses a non-canonical category: {category}"
                )
            if category in categories:
                raise ValueError(
                    f"CHANGELOG.md section {version} has a duplicate category: {category}"
                )
            categories.add(category)
            continue
        if line.startswith("- "):
            if not categories:
                raise ValueError(
                    f"CHANGELOG.md section {version} has a change item outside a category"
                )
            item_count += 1
    if version != "Unreleased" and item_count == 0:
        raise ValueError(f"CHANGELOG.md release {version} must contain a categorized change item")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def check_changelog_provenance(
    releases: list[tuple[str, str]],
    *,
    pending_version: str | None = None,
) -> None:
    """Validate local product tags against the shared Changelog."""
    actual_versions = [version for version, _ in releases]
    expected_versions = known_release_versions()
    if len(actual_versions) != len(set(actual_versions)):
        raise ValueError("released CHANGELOG headings must not duplicate a version")
    missing_headings = [version for version in expected_versions if version not in actual_versions]
    if missing_headings:
        raise ValueError(
            "locally available release tags must appear once in CHANGELOG.md: "
            + ", ".join(missing_headings)
        )
    allowed_pending = pending_version if pending_version not in expected_versions else None
    untagged_headings = [
        version
        for version in actual_versions
        if version not in expected_versions and version != allowed_pending
    ]
    if untagged_headings:
        raise ValueError(
            "released CHANGELOG heading has no product tag: " + ", ".join(untagged_headings)
        )


def check_active_release_train(
    version: str,
    releases: list[tuple[str, str]],
    *,
    pending_release: bool = False,
) -> None:
    """Accept an untagged next version without treating it as published.

    A deployed source tree may contain several ordinary commits between releases.
    The active ``VERSION`` names that next release train, while the dated
    Changelog headings remain an immutable record of tags that already exist.
    """
    known = known_release_versions()
    published = {released for released, _ in releases}
    if version in known:
        if version not in published:
            raise ValueError(f"CHANGELOG.md lacks dated release heading ## [{version}]")
        return
    if version in published:
        latest_heading = releases[0][0] if releases else ""
        if pending_release or (
            version == latest_heading
            and (not known or _version_key(version) > max(map(_version_key, known)))
        ):
            return
        raise ValueError(f"CHANGELOG release {version} exists before its Git tag")
    comparison_set = known + [released for released, _ in releases]
    if not comparison_set:
        raise ValueError("cannot identify an existing release version")
    if _version_key(version) <= max(map(_version_key, comparison_set)):
        raise ValueError(
            f"untagged VERSION {version} must be newer than the latest released version"
        )


def check_release_tag(tag: str, version: str) -> None:
    """Bind an exact annotated tag directly to the ``HEAD`` commit."""
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"tag {tag!r} does not match expected {expected!r}")
    reference = f"refs/tags/{tag}"
    try:
        object_type = _git("cat-file", "-t", reference)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"release tag {tag!r} does not exist") from exc
    if object_type != "tag":
        raise ValueError(f"release tag {tag!r} must be an annotated tag object")
    headers = _git("cat-file", "tag", reference).split("\n\n", 1)[0].splitlines()
    embedded_name = next(
        (line.removeprefix("tag ") for line in headers if line.startswith("tag ")), ""
    )
    if embedded_name != tag:
        raise ValueError(f"release tag {tag!r} embeds tag name {embedded_name!r}, expected {tag!r}")
    target_type = next(
        (line.removeprefix("type ") for line in headers if line.startswith("type ")), ""
    )
    if target_type != "commit":
        raise ValueError(f"release tag {tag!r} must directly name a commit")
    tag_commit = next(
        (line.removeprefix("object ") for line in headers if line.startswith("object ")),
        "",
    )
    head_commit = _git("rev-parse", "HEAD^{commit}")
    if tag_commit != head_commit:
        raise ValueError(
            f"release tag {tag!r} directly names commit {tag_commit}, not HEAD commit {head_commit}"
        )


def check_pending_release_date(
    version: str,
    releases: list[tuple[str, str]],
    *,
    today: date | None = None,
) -> None:
    """Reject a prepared release heading dated after the current UTC day."""
    current_date = today or datetime.now(UTC).date()
    value = next((item_date for item, item_date in releases if item == version), None)
    try:
        release_date = date.fromisoformat(value or "")
    except ValueError as error:
        raise ValueError(f"pending release {version} has no valid release date") from error
    if release_date > current_date:
        raise ValueError(
            f"pending release {version} uses future UTC date {release_date.isoformat()}"
        )


def check_governance_contract() -> None:
    """Validate the repository's release and dual-forge governance surfaces."""
    required = (
        "AGENTS.md",
        "CONTRIBUTING.md",
        "docs/README.md",
        "docs/architecture/authority-and-runtime-boundary.md",
        "docs/governance/release-and-change-policy.md",
        "docs/decisions/decision-register.md",
        "docs/decisions/dr-0001-control-plane-data-plane-boundary.md",
        "docs/evidence/evidence-policy.md",
        "docs/operations/forge-operations.md",
        "LICENSE",
        ".ethos/release.toml",
        "tools/forge/audit.py",
        "tools/release/tag_signature.py",
        "tools/release/publication/__main__.py",
        "tools/release/publication/github/publish.py",
        "tools/release/publication/gitlab/publish.py",
        "tools/reliability/observe.py",
        ".github/workflows/admission.yml",
        ".github/workflows/verify.yml",
    )
    missing = [relative for relative in required if not (ROOT / relative).is_file()]
    if missing:
        raise ValueError("missing governance documents: " + ", ".join(missing))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if not readme.startswith(f"# {product_identity.DISPLAY_NAME}\n"):
        raise ValueError("README.md must use the formal Project Name as its title")
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    if "python -m tools.release.metadata" not in ci:
        raise ValueError("GitLab CI must execute the release and governance checker")
    if "verify-release-tag:" not in ci or "tools.release.tag_signature" not in ci:
        raise ValueError("GitLab CI must verify the exact trusted product tag")
    if "publish-gitlab-release:" in ci or "nox -s release" in ci:
        raise ValueError("GitLab CI must not rebuild or republish the signed product bundle")
    operations = (ROOT / "docs" / "operations" / "forge-operations.md").read_text(encoding="utf-8")
    if "tools.forge.audit" not in operations or "--json" not in operations:
        raise ValueError("forge operations must document the read-only parity audit")
    for token in ("signed once locally", "fast-forward or idempotent"):
        if token not in operations:
            raise ValueError(f"forge operations must document exact object publication: {token}")
    release = tomllib.loads((ROOT / ".ethos" / "release.toml").read_text(encoding="utf-8"))
    protected = release["protected_refs"]["branches"]
    for token in (
        *(f"ethos publish --ref refs/heads/{branch}" for branch in protected),
        "ethos publish --ref refs/heads/proposal/",
        "ethos publish --retire --ref refs/heads/proposal/",
        "`Admission`",
    ):
        if token not in operations:
            raise ValueError(f"forge operations must document ETHOS branch publication: {token}")
    for token in (
        "python -m tools.release.metadata --prepare-release",
        "ethos land --release",
        *(
            f"ethos publish --ref refs/tags/{pattern.removesuffix('*')}"
            for pattern in release["protected_refs"]["tags"]
        ),
    ):
        if token not in operations:
            raise ValueError(f"forge operations must document ETHOS tag publication: {token}")
    for token in ("commit-author-name", "commit-author-email"):
        if token in operations:
            raise ValueError(f"forge operations couple shared history to one actor: {token}")


def _command(
    *,
    tag: Annotated[str | None, Parameter(help="Require an exact v<version> tag.")] = None,
    prepare_release: Annotated[
        bool,
        Parameter(help="Validate a release commit before its product tag exists."),
    ] = False,
    changelog: Annotated[Path | None, Parameter(show=False)] = None,
) -> None:
    """Validate release identity and repository governance."""
    if prepare_release and tag:
        raise SystemExit("--prepare-release cannot be combined with --tag")
    version = read_version()
    check_python_metadata()
    releases = changelog_releases(changelog)
    check_changelog_provenance(
        releases,
        pending_version=version,
    )
    if prepare_release:
        if version in known_release_versions():
            raise SystemExit(f"release tag v{version} already exists; use --tag validation instead")
        current_heading = f"## [{version}] - "
        heading = next((f"## [{item}] - {date}" for item, date in releases if item == version), "")
        if not heading.startswith(current_heading):
            raise SystemExit(
                f"CHANGELOG.md lacks pending release heading ## [{version}] - YYYY-MM-DD"
            )
        first_published = releases[0][0] if releases else ""
        if first_published != version:
            raise SystemExit(
                f"pending release {version} must be the first published CHANGELOG section"
            )
        try:
            check_pending_release_date(version, releases)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    try:
        check_active_release_train(
            version,
            releases,
            pending_release=prepare_release,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    proxy = (ROOT / "src" / "openai_responses_proxy" / "serving" / "entrypoint.py").read_text(
        encoding="utf-8"
    )
    if "release_version()" not in proxy:
        raise SystemExit("proxy runtime header does not read VERSION")
    try:
        check_governance_contract()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if tag:
        try:
            check_release_tag(tag, version)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if version not in {released for released, _ in releases}:
            raise SystemExit(f"CHANGELOG.md lacks dated release heading ## [{version}]")
    print(f"release and governance metadata: {version} OK")


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run release validation through the repository's single parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
