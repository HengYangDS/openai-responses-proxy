"""Read-only GitHub Actions and Release evidence for one exact tag commit."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle.records import is_version
from openai_responses_proxy.lifecycle.records import version_key
from tools.release import identity
from tools.release.artifact import format as release_assets
from tools.release.publication import hosted

DEFAULT_REQUIRED_JOBS: Final = (
    "Resolve supported Python versions",
    "Tag metadata and governance",
    "Native asset (linux-x86_64)",
    "Native asset (macos-arm64)",
    "Native asset (windows-x86_64)",
    "Release assets",
)


class GitHubProofError(RuntimeError):
    """GitHub hosted evidence is missing, ambiguous, or mismatched."""


ROOT = Path(__file__).resolve().parents[4]


def published_predecessor(*, repository: str, version: str, root: Path = ROOT) -> str:
    """Return the newest published release tag that is an ancestor of ``HEAD``."""
    if not repository or not is_version(version):
        raise GitHubProofError("published predecessor request is invalid")
    malformed = "GitHub release response is malformed"
    releases = [
        release
        for page in _api_pages(f"repos/{repository}/releases?per_page=100")
        for release in _mappings(page, malformed)
    ]
    current = version_key(version)
    candidates: list[str] = []
    for release in releases:
        tag = release.get("tag_name")
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        if not isinstance(tag, str) or not identity.is_tag(tag):
            raise GitHubProofError("GitHub published release tag is invalid")
        if version_key(identity.version_from_tag(tag)) < current:
            candidates.append(tag)
    if len(candidates) != len(set(candidates)):
        raise GitHubProofError("GitHub published predecessor identity is ambiguous")
    for tag in sorted(
        candidates,
        key=lambda item: version_key(identity.version_from_tag(item)),
        reverse=True,
    ):
        result = subprocess.run(
            ("git", "merge-base", "--is-ancestor", f"refs/tags/{tag}^{{commit}}", "HEAD"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode == 0:
            return tag
        if result.returncode != 1:
            raise GitHubProofError(f"local release ancestry is unavailable for {tag}")
    raise GitHubProofError("published predecessor is unavailable")


def _mapping(value: object, unavailable: str) -> Mapping[str, object]:
    """Require one mapping-shaped GitHub API object."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise GitHubProofError(unavailable)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _mappings(value: object, malformed: str) -> list[Mapping[str, object]]:
    """Require a JSON array containing only object records."""
    if not isinstance(value, list):
        raise GitHubProofError(malformed)
    return [_mapping(item, malformed) for item in value]


def _one(records: Sequence[Mapping[str, object]], message: str) -> Mapping[str, object]:
    """Require one unambiguous GitHub record."""
    if len(records) != 1:
        raise GitHubProofError(message)
    return records[0]


def _object_pages(endpoint: str, key: str, malformed: str) -> list[Mapping[str, object]]:
    """Flatten paginated object records after validating every envelope."""
    records: list[Mapping[str, object]] = []
    for page in _api_pages(endpoint):
        items = _mapping(page, malformed).get(key)
        records.extend(_mappings(items, malformed))
    return records


def normalize(
    *,
    repository: str,
    tag: str,
    commit_oid: str,
    runs: Sequence[Mapping[str, object]],
    jobs: Mapping[int, Sequence[Mapping[str, object]]],
    release: Mapping[str, object],
    assets: Mapping[str, bytes],
    required_jobs: Sequence[str] = DEFAULT_REQUIRED_JOBS,
) -> dict[str, object]:
    """Normalize exact successful Verify workflow and Release record evidence."""
    tagged_runs = [
        run
        for run in runs
        if run.get("path") == ".github/workflows/verify.yml"
        and run.get("head_branch") == tag
        and run.get("event") == "push"
    ]
    run = _one(tagged_runs, "GitHub Verify run is missing or ambiguous")
    if (
        run.get("name"),
        run.get("event"),
        run.get("head_sha"),
        run.get("status"),
        run.get("conclusion"),
    ) != ("Verify", "push", commit_oid, "completed", "success"):
        raise GitHubProofError("GitHub Verify run identity does not match")
    run_id = run.get("id")
    if isinstance(run_id, bool) or not isinstance(run_id, int):
        raise GitHubProofError("GitHub workflow run has no stable id")
    statuses: dict[str, str] = {}
    for job in jobs.get(run_id, ()):
        name = job.get("name")
        if not isinstance(name, str) or name not in required_jobs:
            continue
        if name in statuses:
            raise GitHubProofError("GitHub required job identity is ambiguous")
        statuses[name] = (
            str(job.get("conclusion")) if job.get("status") == "completed" else "incomplete"
        )
    if any(statuses.get(name) != "success" for name in required_jobs):
        raise GitHubProofError("GitHub required jobs are incomplete or unsuccessful")
    release_id = release.get("id")
    published_at = release.get("published_at")
    if not all(
        (
            isinstance(release_id, int) and not isinstance(release_id, bool),
            release.get("tag_name") == tag,
            release.get("name") == product_identity.release_title(tag),
            release.get("draft") is False,
            release.get("prerelease") is False,
            isinstance(published_at, str) and bool(published_at),
        )
    ):
        raise GitHubProofError("GitHub release identity does not match")
    return {
        "repository": repository,
        "ci": {
            "id": run_id,
            "revision_oid": commit_oid,
            "status": "success",
            "jobs": statuses,
        },
        "release": {
            "id": release_id,
            "tag": tag,
            "commit_oid": commit_oid,
            "name": release["name"],
            "draft": False,
            "prerelease": False,
        },
        "assets": release_assets.release_digests(
            assets, identity.version_from_tag(tag), release_assets.RELEASE_PLATFORMS
        ),
    }


def collect(
    *,
    repository: str,
    tag: str,
    tag_object_oid: str,
    commit_oid: str,
    required_jobs: Sequence[str] = DEFAULT_REQUIRED_JOBS,
) -> dict[str, object]:
    """Query GitHub and bind API identity to independently fetched Git objects."""
    ref = _mapping(
        _api(f"repos/{repository}/git/ref/tags/{tag}"),
        "GitHub tag ref response is malformed",
    )
    ref_object = _mapping(ref.get("object"), "GitHub tag ref object is unavailable")
    if (ref.get("ref"), ref_object.get("type"), ref_object.get("sha")) != (
        f"refs/tags/{tag}",
        "tag",
        tag_object_oid,
    ):
        raise GitHubProofError("GitHub API tag object differs from fetched tag")
    tag_object = _mapping(
        _api(f"repos/{repository}/git/tags/{tag_object_oid}"),
        "GitHub tag object response is malformed",
    )
    target = _mapping(tag_object.get("object"), "GitHub tag target is unavailable")
    if (
        tag_object.get("tag"),
        tag_object.get("sha"),
        target.get("type"),
        target.get("sha"),
    ) != (tag, tag_object_oid, "commit", commit_oid):
        raise GitHubProofError("GitHub tag identity does not match fetched Git objects")

    runs = _object_pages(
        f"repos/{repository}/actions/workflows/verify.yml/runs?branch={tag}&per_page=100",
        "workflow_runs",
        "GitHub workflow response is malformed",
    )
    jobs = {
        run_id: _object_pages(
            f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
            "jobs",
            "GitHub jobs response is malformed",
        )
        for run in runs
        if isinstance(run_id := run.get("id"), int) and not isinstance(run_id, bool)
    }
    malformed = "GitHub release response is malformed"
    releases = [
        release
        for page in _api_pages(f"repos/{repository}/releases?per_page=100")
        for release in _mappings(page, malformed)
    ]
    matches = [release for release in releases if release.get("tag_name") == tag]
    typed_release = _one(matches, "GitHub release record is missing or ambiguous")
    asset_records = _mappings(typed_release.get("assets"), "GitHub release assets are malformed")
    expected_names = release_assets.release_asset_names(
        identity.version_from_tag(tag), release_assets.RELEASE_PLATFORMS
    )
    selected_assets = {
        str(record.get("name")): hosted.api_bytes(
            (
                hosted.executable("gh", GitHubProofError),
                "api",
                "--method",
                "GET",
                "-H",
                "Accept: application/octet-stream",
                str(record.get("url")),
            ),
            unavailable="GitHub release asset is unavailable",
            error_type=GitHubProofError,
        )
        for record in asset_records
        if record.get("name") in expected_names and isinstance(record.get("url"), str)
    }
    if set(selected_assets) != expected_names:
        raise GitHubProofError("GitHub release asset set is incomplete or ambiguous")
    return normalize(
        repository=repository,
        tag=tag,
        commit_oid=commit_oid,
        runs=runs,
        jobs=jobs,
        release=typed_release,
        assets=selected_assets,
        required_jobs=required_jobs,
    )


def _api(endpoint: str) -> object:
    return hosted.api_json(
        (hosted.executable("gh", GitHubProofError), "api", "--method", "GET", endpoint),
        unavailable="GitHub API evidence is unavailable",
        error_type=GitHubProofError,
    )


def _api_pages(endpoint: str) -> list[object]:
    value = hosted.api_json(
        (
            hosted.executable("gh", GitHubProofError),
            "api",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            endpoint,
        ),
        unavailable="GitHub paginated API evidence is unavailable",
        error_type=GitHubProofError,
    )
    if not isinstance(value, list):
        raise GitHubProofError("GitHub paginated API response is malformed")
    return list(value)
