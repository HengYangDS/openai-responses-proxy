"""Publish one immutable GitLab-native release asset set."""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path

from openai_responses_proxy import product_identity
from tools.release import identity
from tools.release.artifact import assembly

# A provider error body is reported up to this many bytes.
_ERROR_DETAIL_BYTES = 2048


class GitLabPublishError(RuntimeError):
    """GitLab publication failed or conflicts with immutable identity."""


class _GitLabResourceMissingError(GitLabPublishError):
    """One exact GitLab publication resource does not exist."""


class CredentialKind(StrEnum):
    """GitLab credential semantics admitted by the publication adapter."""

    JOB_TOKEN = "job-token"
    PRIVATE_TOKEN = "private-token"


def _authentication_header(kind: CredentialKind) -> str:
    """Return the GitLab header owned by one declared credential kind."""
    match kind:
        case CredentialKind.JOB_TOKEN:
            return "JOB-TOKEN"
        case CredentialKind.PRIVATE_TOKEN:
            return "PRIVATE-TOKEN"


def _request(
    url: str,
    token: str,
    credential_kind: CredentialKind,
    *,
    data: bytes | None = None,
    method: str = "GET",
) -> bytes:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header(_authentication_header(credential_kind), token)
    if data is not None and method == "POST":
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            content: object = response.read()
            if not isinstance(content, bytes):
                raise GitLabPublishError("GitLab publication response is not binary")
            return content
    except urllib.error.HTTPError as error:
        try:
            detail = _error_detail(error)
            if error.code == HTTPStatus.NOT_FOUND:
                raise _GitLabResourceMissingError(f"GitLab resource is missing{detail}") from error
            if error.code == HTTPStatus.CONFLICT:
                raise FileExistsError(url) from error
            raise GitLabPublishError(
                f"GitLab publication failed with HTTP {error.code}{detail}"
            ) from error
        finally:
            error.close()
    except (OSError, urllib.error.URLError) as error:
        raise GitLabPublishError("GitLab publication transport failed") from error


def _verify(assets: Path, trust: str) -> list[str]:
    """Verify one complete pre-signed bundle without changing its bytes."""
    try:
        return sorted(assembly.verify(assets, trust=trust))
    except (OSError, ValueError) as error:
        raise GitLabPublishError("release asset signature verification failed") from error


def publish(
    *,
    api_base: str,
    project_id: int,
    tag: str,
    token: str,
    credential_kind: CredentialKind,
    source: Path,
    trust: str,
) -> str:
    """Upload, re-download, verify, then create or validate one GitLab Release."""
    if not api_base.startswith(("http://", "https://")) or not identity.is_tag(tag):
        raise GitLabPublishError("GitLab API base or release tag is invalid")
    if project_id < 1 or not source.is_dir() or source.is_symlink():
        raise GitLabPublishError("GitLab project or release asset directory is invalid")
    with tempfile.TemporaryDirectory(
        prefix=f"{product_identity.PRODUCT_SLUG}-gitlab-release-"
    ) as name:
        root, downloaded = Path(name) / "assets", Path(name) / "downloaded"
        shutil.copytree(source, root)
        downloaded.mkdir()
        names = _verify(root, trust)
        asset_base = (
            f"{api_base.rstrip('/')}/projects/{project_id}/packages/generic/"
            f"{product_identity.PRODUCT_SLUG}/{tag}"
        )
        links = [
            {"name": item, "url": f"{asset_base}/{item}", "link_type": "package"} for item in names
        ]
        release = {
            "tag_name": tag,
            "name": product_identity.release_title(tag),
            "description": (
                "Provider-native source release. See CHANGELOG.md for user-relevant changes."
            ),
            "assets": {"links": links},
        }
        endpoint = f"{api_base.rstrip('/')}/projects/{project_id}/releases"
        try:
            existing = json.loads(_request(f"{endpoint}/{tag}", token, credential_kind))
        except _GitLabResourceMissingError:
            existing = None
        else:
            _require_matching_release(existing, release, links)
        for asset_name in names:
            payload = (root / asset_name).read_bytes()
            try:
                received = _request(f"{asset_base}/{asset_name}", token, credential_kind)
            except _GitLabResourceMissingError:
                with contextlib.suppress(FileExistsError):
                    _request(
                        f"{asset_base}/{asset_name}",
                        token,
                        credential_kind,
                        data=payload,
                        method="PUT",
                    )
                received = _request(f"{asset_base}/{asset_name}", token, credential_kind)
            if received != payload:
                raise GitLabPublishError(
                    f"GitLab release asset differs before upload: {asset_name}"
                )
            (downloaded / asset_name).write_bytes(received)
        if _verify(downloaded, trust) != names:
            raise GitLabPublishError("GitLab release asset inventory differs after upload")
        result = "matched"
        if existing is None:
            try:
                _request(
                    endpoint,
                    token,
                    credential_kind,
                    data=json.dumps(release).encode(),
                    method="POST",
                )
                result = "created"
            except FileExistsError:
                pass
        persisted = json.loads(_request(f"{endpoint}/{tag}", token, credential_kind))
        _require_matching_release(persisted, release, links)
        return result


def _require_matching_release(
    existing: object, expected: Mapping[str, object], expected_links: list[dict[str, str]]
) -> None:
    """Require one existing Release to equal the requested immutable identity."""
    if not isinstance(existing, Mapping):
        raise GitLabPublishError("existing GitLab release does not match immutable identity")
    assets = existing.get("assets")
    links = assets.get("links") if isinstance(assets, Mapping) else None
    observed_links: list[dict[str, str]] = []
    if isinstance(links, list):
        for link in links:
            match link:
                case {"name": str(name), "url": str(url), "link_type": str(link_type)}:
                    observed_links.append({"name": name, "url": url, "link_type": link_type})
                case _:
                    raise GitLabPublishError(
                        "existing GitLab release does not match immutable identity"
                    )
    if (
        existing.get("tag_name") != expected["tag_name"]
        or existing.get("name") != expected["name"]
        or existing.get("description") != expected["description"]
        or sorted(observed_links, key=lambda link: link["name"]) != expected_links
    ):
        raise GitLabPublishError("existing GitLab release does not match immutable identity")


def _error_detail(error: urllib.error.HTTPError) -> str:
    """Return one bounded, credential-free provider diagnostic."""
    try:
        raw = error.read(_ERROR_DETAIL_BYTES + 1)
    except OSError:
        return ""
    if len(raw) > _ERROR_DETAIL_BYTES:
        raw = raw[:_ERROR_DETAIL_BYTES]
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        message = payload.get("message") if isinstance(payload, dict) else payload
        detail = message if isinstance(message, str) else json.dumps(message, sort_keys=True)
    return f": {detail[:512]}" if detail else ""
