"""Read-only GitLab pipeline and Release evidence for one exact tag commit."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Final
from typing import cast

from openai_responses_proxy import product_identity
from tools.release import identity
from tools.release.artifact import format as release_assets
from tools.release.publication import hosted

DEFAULT_REQUIRED_JOBS: Final = ("verify-release-tag",)


class GitLabProofError(RuntimeError):
    """GitLab hosted evidence is missing, ambiguous, or mismatched."""


def _mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise GitLabProofError(message)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _single(items: Sequence[object], message: str) -> object:
    if len(items) != 1:
        raise GitLabProofError(message)
    return items[0]


def _require_subset(
    expected: Mapping[str, object], actual: Mapping[str, object], message: str
) -> None:
    if not expected.items() <= actual.items():
        raise GitLabProofError(message)


def _stable_id(value: object) -> int:
    if type(value) is not int:
        raise GitLabProofError("GitLab pipeline has no stable id")
    return value


def _evidence(value: object) -> Mapping[str, object]:
    message = "GitLab release evidence identity is unavailable"
    if not isinstance(value, list):
        raise GitLabProofError(message)
    return _mapping(_single(value, message), message)


def _page_items(page: object) -> list[object]:
    if isinstance(page, list):
        return list(page)
    if isinstance(page, dict):
        return [page]
    raise GitLabProofError("GitLab paginated API response is malformed")


def normalize(
    *,
    repository: str,
    tag: str,
    commit_oid: str,
    pipeline: Mapping[str, object],
    jobs: Sequence[Mapping[str, object]],
    release: Mapping[str, object],
    assets: Mapping[str, bytes],
    required_jobs: Sequence[str] = DEFAULT_REQUIRED_JOBS,
) -> dict[str, object]:
    """Normalize one exact successful tag pipeline and release record."""
    pipeline_identity = {
        "sha": commit_oid,
        "ref": tag,
        "tag": True,
        "source": "push",
        "status": "success",
    }
    _require_subset(pipeline_identity, pipeline, "GitLab tag pipeline identity does not match")
    if pipeline.get("yaml_errors") not in (None, ""):
        raise GitLabProofError("GitLab tag pipeline identity does not match")

    statuses: dict[str, str] = {}
    for job in jobs:
        name = job.get("name")
        if name not in required_jobs:
            continue
        name = cast("str", name)
        if job.get("allow_failure") is not False:
            raise GitLabProofError("GitLab required job permits failure")
        nested_pipeline = _mapping(
            job.get("pipeline"),
            "GitLab required job identity does not match the tag pipeline",
        )
        commit = _mapping(
            job.get("commit"),
            "GitLab required job identity does not match the tag pipeline",
        )
        job_identity = {"ref": tag, "tag": True}
        pipeline_job_identity = {
            "id": pipeline.get("id"),
            "sha": commit_oid,
            "ref": tag,
            "source": "push",
        }
        message = "GitLab required job identity does not match the tag pipeline"
        _require_subset(job_identity, job, message)
        _require_subset(pipeline_job_identity, nested_pipeline, message)
        if commit.get("id") != commit_oid:
            raise GitLabProofError(message)
        if name in statuses:
            raise GitLabProofError("GitLab required job identity is ambiguous")
        statuses[name] = str(job.get("status"))
    if set(statuses.items()) != {(name, "success") for name in required_jobs}:
        raise GitLabProofError("GitLab required jobs are incomplete or unsuccessful")

    release_commit = _mapping(release.get("commit"), "GitLab release commit is unavailable")
    release_identity = {
        "tag_name": tag,
        "name": product_identity.release_title(tag),
        "upcoming_release": False,
        "description": (
            "Provider-native source release. See CHANGELOG.md for user-relevant changes."
        ),
    }
    _require_subset(release_identity, release, "GitLab release identity does not match")
    if release_commit.get("id") != commit_oid:
        raise GitLabProofError("GitLab release identity does not match")
    evidence = _evidence(release.get("evidences"))
    evidence_sha = evidence.get("sha")
    if not isinstance(evidence_sha, str) or not evidence_sha:
        raise GitLabProofError("GitLab release evidence identity is unavailable")
    return {
        "repository": repository,
        "ci": {
            "id": pipeline.get("id"),
            "revision_oid": commit_oid,
            "status": "success",
            "jobs": statuses,
        },
        "release": {
            "id": evidence_sha,
            "tag": tag,
            "commit_oid": commit_oid,
            "name": release["name"],
            "draft": False,
            "prerelease": False,
        },
        "assets": release_assets.release_digests(
            assets,
            identity.version_from_tag(tag),
            release_assets.release_platforms(set(assets), identity.version_from_tag(tag)),
        ),
    }


def collect(
    *,
    api_base: str,
    repository: str,
    tag: str,
    tag_object_oid: str,
    commit_oid: str,
    required_jobs: Sequence[str] = DEFAULT_REQUIRED_JOBS,
) -> dict[str, object]:
    """Query GitLab through authenticated read-only API calls."""
    base = api_base.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise GitLabProofError("GitLab API base must be HTTP(S)")
    project, encoded_tag = (urllib.parse.quote(value, safe="") for value in (repository, tag))
    tag_record = _mapping(
        _api(f"{base}/projects/{project}/repository/tags/{encoded_tag}"),
        "GitLab API tag object differs from fetched tag",
    )
    _require_subset(
        {"name": tag, "target": tag_object_oid},
        tag_record,
        "GitLab API tag object differs from fetched tag",
    )
    commit = _mapping(tag_record.get("commit"), "GitLab API tag commit differs from fetched tag")
    if commit.get("id") != commit_oid:
        raise GitLabProofError("GitLab API tag commit differs from fetched tag")

    pipelines = _api_pages(
        f"{base}/projects/{project}/pipelines?ref={encoded_tag}&sha={commit_oid}&per_page=100"
    )
    expected_pipeline = {"ref": tag, "sha": commit_oid}
    matches = [
        item
        for item in pipelines
        if isinstance(item, Mapping)
        if expected_pipeline.items() <= item.items()
    ]
    listed = _mapping(
        _single(matches, "GitLab successful tag pipeline is missing or ambiguous"),
        "GitLab successful tag pipeline is missing or ambiguous",
    )
    listed_id = _stable_id(listed.get("id"))
    pipeline = _mapping(
        _api(f"{base}/projects/{project}/pipelines/{listed_id}"),
        "GitLab tag pipeline detail is malformed",
    )
    pipeline_id = _stable_id(pipeline.get("id"))
    if pipeline_id != listed_id:
        raise GitLabProofError("GitLab pipeline detail identity does not match listing")

    malformed = "GitLab hosted evidence is malformed"
    jobs = [
        _mapping(item, malformed)
        for item in _api_pages(
            f"{base}/projects/{project}/pipelines/{pipeline_id}/jobs?per_page=100&include_retried=false"
        )
    ]
    releases = _api_pages(f"{base}/projects/{project}/releases?per_page=100")
    release_records = [_mapping(item, malformed) for item in releases]
    release_matches = [item for item in release_records if item.get("tag_name") == tag]
    release = _mapping(
        _single(release_matches, "GitLab release record is missing or ambiguous"),
        "GitLab hosted evidence is malformed",
    )
    assets = _mapping(release.get("assets"), "GitLab release assets are unavailable")
    links = assets.get("links")
    if not isinstance(links, list):
        raise GitLabProofError("GitLab release assets are unavailable")
    glab = hosted.executable("glab", GitLabProofError)
    selected_assets = {
        str(link.get("name")): hosted.api_bytes(
            (glab, "api", "--method", "GET", str(link.get("url"))),
            unavailable="GitLab release asset is unavailable",
            error_type=GitLabProofError,
        )
        for item in links
        if isinstance(item, Mapping)
        for link in [_mapping(item, "GitLab release asset link is malformed")]
        if isinstance(link.get("name"), str) and isinstance(link.get("url"), str)
    }
    return normalize(
        repository=repository,
        tag=tag,
        commit_oid=commit_oid,
        pipeline=pipeline,
        jobs=jobs,
        release=release,
        assets=selected_assets,
        required_jobs=required_jobs,
    )


def _api(endpoint: str) -> object:
    return hosted.api_json(
        (
            hosted.executable("glab", GitLabProofError),
            "api",
            "--method",
            "GET",
            endpoint,
        ),
        unavailable="GitLab API evidence is unavailable",
        error_type=GitLabProofError,
    )


def _api_pages(endpoint: str) -> list[object]:
    executable = hosted.executable("glab", GitLabProofError)
    try:
        completed = subprocess.run(
            (
                executable,
                "api",
                "--method",
                "GET",
                "--paginate",
                "--output",
                "ndjson",
                endpoint,
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        pages = list(map(json.loads, filter(str.strip, completed.stdout.splitlines())))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise GitLabProofError("GitLab paginated API evidence is unavailable") from error
    return [item for page in pages for item in _page_items(page)]
