"""Publish one immutable GitHub-native release asset set."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path

from openai_responses_proxy import product_identity
from tools.release import identity
from tools.release import tag_signature
from tools.release.artifact import assembly
from tools.release.publication import hosted


class GitHubPublishError(RuntimeError):
    """GitHub publication failed or conflicts with immutable identity."""


def select_release(
    releases: Sequence[Mapping[str, object]], tag: str
) -> Mapping[str, object] | None:
    """Return the exact immutable release record, or ``None`` before creation."""
    matches = [release for release in releases if release.get("tag_name") == tag]
    if not matches:
        return None
    if len(matches) != 1:
        raise GitHubPublishError("duplicate GitHub release records for exact tag")
    release = matches[0]
    if not all(
        (
            isinstance(release.get("id"), int) and not isinstance(release.get("id"), bool),
            release.get("name") == product_identity.release_title(tag),
            release.get("draft") is False,
            release.get("prerelease") is False,
            isinstance(release.get("published_at"), str) and bool(release.get("published_at")),
        )
    ):
        raise GitHubPublishError("existing GitHub release does not match exact release identity")
    return release


def verify_remote_tag(
    *,
    ref: Mapping[str, object],
    tag_object: Mapping[str, object],
    tag: str,
    tag_oid: str,
    commit_oid: str,
) -> None:
    """Bind GitHub's annotated tag API identity to local immutable Git objects."""
    reference = ref.get("object")
    target = tag_object.get("object")
    if (
        ref.get("ref") != f"refs/tags/{tag}"
        or not isinstance(reference, Mapping)
        or (reference.get("type"), reference.get("sha")) != ("tag", tag_oid)
        or tag_object.get("tag") != tag
        or tag_object.get("sha") != tag_oid
        or not isinstance(target, Mapping)
        or (target.get("type"), target.get("sha")) != ("commit", commit_oid)
    ):
        raise GitHubPublishError("GitHub release tag does not match local immutable objects")


def publish(
    *,
    repository: str,
    tag: str,
    commit_oid: str,
    checkout: Path,
    tag_trust: str,
    asset_trust: str,
    source: Path,
    workspace: Path,
) -> str:
    """Verify source, publish one exact release, and prove downloaded byte parity."""
    if (
        not repository
        or not identity.is_tag(tag)
        or len(commit_oid) not in {40, 64}
        or not checkout.is_dir()
        or not tag_trust.strip()
        or not asset_trust.strip()
        or not source.is_dir()
        or source.is_symlink()
        or (workspace.exists() and (workspace.is_symlink() or any(workspace.iterdir())))
    ):
        raise GitHubPublishError("GitHub publication inputs are invalid")
    workspace.mkdir(parents=True, exist_ok=True)
    tag_oid, checked_commit = _local_tag_identity(checkout, tag, commit_oid)
    _verify_source(checkout, tag, tag_trust)
    _verify_remote_identity(repository, tag, tag_oid, checked_commit)
    existing = select_release(_release_records(repository), tag)
    source_digests = _verify_assets(source, asset_trust)
    state = "matched"
    if existing is None:
        _create_release(repository, tag, source)
        state = "created"
    downloaded = _download_release_assets(repository, tag, workspace / "downloaded-assets")
    downloaded_digests = _verify_assets(downloaded, asset_trust)
    if downloaded_digests != source_digests or _file_bytes(downloaded) != _file_bytes(source):
        raise GitHubPublishError("GitHub release assets differ after publication")
    return state


def _local_tag_identity(checkout: Path, tag: str, commit_oid: str) -> tuple[str, str]:
    """Validate one local annotated release tag without checkout mutation."""
    git = hosted.executable("git", GitHubPublishError)
    if _output((git, "-C", str(checkout), "cat-file", "-t", f"refs/tags/{tag}")) != "tag":
        raise GitHubPublishError("GitHub release tag is not annotated")
    tag_oid = _output((git, "-C", str(checkout), "rev-parse", f"refs/tags/{tag}^{{tag}}"))
    target = _output((git, "-C", str(checkout), "rev-parse", f"refs/tags/{tag}^{{commit}}"))
    if target != commit_oid:
        raise GitHubPublishError("GitHub release tag differs from the verified commit")
    return tag_oid, target


def _verify_source(checkout: Path, tag: str, trust: str) -> None:
    with tempfile.TemporaryDirectory(
        prefix=f"{product_identity.PRODUCT_SLUG}-github-tag-trust-"
    ) as name:
        anchor = Path(name) / "allowed-signers"
        anchor.write_text(trust.rstrip("\n") + "\n", encoding="utf-8")
        try:
            tag_signature.verify(checkout, tag, anchor)
        except tag_signature.TagSignatureError as error:
            raise GitHubPublishError("GitHub release tag signature is invalid") from error
    # Keep the active repository environment; resolving a venv executable
    # escapes it to the system interpreter on hosted runners.
    _run(
        (
            sys.executable,
            "-B",
            "-m",
            "tools.release.metadata",
            "--tag",
            tag,
        ),
        "GitHub release metadata is invalid",
        cwd=checkout,
    )


def _verify_remote_identity(repository: str, tag: str, tag_oid: str, commit_oid: str) -> None:
    gh = hosted.executable("gh", GitHubPublishError)
    ref = _api_mapping((gh, "api", f"repos/{repository}/git/ref/tags/{tag}"))
    tag_object = _api_mapping((gh, "api", f"repos/{repository}/git/tags/{tag_oid}"))
    verify_remote_tag(
        ref=ref,
        tag_object=tag_object,
        tag=tag,
        tag_oid=tag_oid,
        commit_oid=commit_oid,
    )


def _release_records(repository: str) -> list[Mapping[str, object]]:
    gh = hosted.executable("gh", GitHubPublishError)
    value = hosted.api_json(
        (
            gh,
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/releases?per_page=100",
        ),
        unavailable="GitHub release records are unavailable",
        error_type=GitHubPublishError,
    )
    if not isinstance(value, list):
        raise GitHubPublishError("GitHub release records are malformed")
    releases: list[Mapping[str, object]] = []
    for page in value:
        if not isinstance(page, list) or any(not isinstance(item, Mapping) for item in page):
            raise GitHubPublishError("GitHub release records are malformed")
        releases.extend(page)
    return releases


def _verify_assets(root: Path, trust: str) -> dict[str, str]:
    try:
        return assembly.verify(root, trust=trust)
    except (OSError, ValueError) as error:
        raise GitHubPublishError("GitHub release assets are invalid") from error


def _create_release(repository: str, tag: str, assets: Path) -> None:
    gh = hosted.executable("gh", GitHubPublishError)
    names = sorted(path for path in assets.iterdir() if path.is_file())
    if not names:
        raise GitHubPublishError("GitHub release asset set is empty")
    _run(
        (
            gh,
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--verify-tag",
            "--title",
            product_identity.release_title(tag),
            "--generate-notes",
            *(str(path) for path in names),
        ),
        "GitHub release creation failed",
    )


def _download_release_assets(repository: str, tag: str, target: Path) -> Path:
    gh = hosted.executable("gh", GitHubPublishError)
    target.mkdir(parents=True, exist_ok=False)
    _run(
        (
            gh,
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--dir",
            str(target),
            "--pattern",
            f"{product_identity.PRODUCT_SLUG}-*",
            "--pattern",
            "SHA256SUMS*",
        ),
        "GitHub release assets cannot be downloaded",
    )
    return target


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def _api_mapping(command: Sequence[str]) -> Mapping[str, object]:
    value = hosted.api_json(
        command,
        unavailable="GitHub release identity is unavailable",
        error_type=GitHubPublishError,
    )
    if not isinstance(value, Mapping):
        raise GitHubPublishError("GitHub release identity is malformed")
    if not all(isinstance(key, str) for key in value):
        raise GitHubPublishError("GitHub release identity is malformed")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _run(command: Sequence[str], unavailable: str, *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitHubPublishError(unavailable) from error


def _output(command: Sequence[str]) -> str:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitHubPublishError("GitHub release Git identity is unavailable") from error
