"""GitLab-native release publication contracts."""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import TypedDict

import pytest

from tests.release.artifact.fixtures import release_bundle
from tools.release.artifact import format as assets
from tools.release.artifact import signing
from tools.release.publication.gitlab import publish


class _PublicationArguments(TypedDict):
    api_base: str
    project_id: int
    tag: str
    token: str
    credential_kind: publish.CredentialKind
    source: Path
    trust: str


def _assets(root: Path, version: str) -> None:
    release = release_bundle(version=version)
    del release[assets.SIGNATURE_NAME]
    for name, content in release.items():
        (root / name).write_bytes(content)


@pytest.mark.parametrize("create_conflict", [False, True], ids=["created", "raced"])
@pytest.mark.parametrize("link_field", ["name", "url", "link_type"])
@pytest.mark.parametrize("corrupt_on_create", [False, True], ids=["existing", "persisted"])
def test_publication_creates_and_accepts_only_exact_existing_release(
    tmp_path: Path,
    mocker,
    *,
    create_conflict: bool,
    link_field: str,
    corrupt_on_create: bool,
) -> None:
    assets, key = tmp_path / "assets", tmp_path / "signing"
    assets.mkdir()
    _assets(assets, "1.2.3")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    signing.sign_and_verify(assets=assets, key=key, trust=trust)
    expected = {path.name: path.read_bytes() for path in assets.iterdir()}
    store: dict[str, list[bytes]] = {}
    release: dict[str, object] = {}
    uploads: list[str] = []

    def request(
        url: str,
        _token: str,
        _credential_kind: publish.CredentialKind,
        *,
        data: bytes | None = None,
        method: str = "GET",
    ) -> bytes:
        name = url.rsplit("/", 1)[-1]
        if "/packages/generic/" in url:
            if method == "PUT":
                assert data is not None
                uploads.append(name)
                store.setdefault(name, []).append(data)
                return b""
            if name not in store:
                raise publish._GitLabResourceMissingError(url)
            return store[name][-1]
        if method == "POST":
            if release:
                raise FileExistsError(url)
            assert data is not None
            persisted = json.loads(data)
            if corrupt_on_create:
                persisted["assets"]["links"][0][link_field] = "wrong"
            release.update(persisted)
            if create_conflict:
                raise FileExistsError(url)
            return b"{}"
        if not release:
            raise publish._GitLabResourceMissingError(url)
        return json.dumps(release).encode()

    mocker.patch.object(publish, "_request", side_effect=request)
    arguments: _PublicationArguments = {
        "api_base": "https://gitlab.example/api/v4",
        "project_id": 101,
        "tag": "v1.2.3",
        "token": "redacted",
        "credential_kind": publish.CredentialKind.JOB_TOKEN,
        "source": assets,
        "trust": trust,
    }
    if corrupt_on_create:
        with pytest.raises(publish.GitLabPublishError, match="immutable identity"):
            publish.publish(**arguments)
        return
    assert publish.publish(**arguments) == ("matched" if create_conflict else "created")
    assert {name: values[-1] for name, values in store.items()} == expected
    assert uploads == sorted(expected)
    assert publish.publish(**arguments) == "matched"
    assert uploads == sorted(expected)
    assert all(len(values) == 1 for values in store.values())
    release_assets = release["assets"]
    assert isinstance(release_assets, dict)
    links = release_assets["links"]
    links.reverse()
    for index, link in enumerate(links):
        link["id"] = index
    assert publish.publish(**arguments) == "matched"
    original = links[0][link_field]
    links[0][link_field] = "wrong"
    with pytest.raises(publish.GitLabPublishError, match="immutable identity"):
        publish.publish(**arguments)
    links[0][link_field] = original
    release["name"] = "wrong"
    with pytest.raises(publish.GitLabPublishError, match="immutable identity"):
        publish.publish(**arguments)


def test_publication_reuses_exact_partial_package_and_uploads_only_missing_assets(
    tmp_path: Path, mocker
) -> None:
    assets, key = tmp_path / "assets", tmp_path / "signing"
    assets.mkdir()
    _assets(assets, "1.2.3")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    signing.sign_and_verify(assets=assets, key=key, trust=trust)
    names = sorted(path.name for path in assets.iterdir())
    present = names[0]
    store = {present: (assets / present).read_bytes()}
    uploads: list[str] = []
    release: dict[str, object] = {}

    def request(
        url: str,
        _token: str,
        _credential_kind: publish.CredentialKind,
        *,
        data: bytes | None = None,
        method: str = "GET",
    ) -> bytes:
        name = url.rsplit("/", 1)[-1]
        if "/packages/generic/" in url:
            if method == "PUT":
                assert data is not None
                uploads.append(name)
                store[name] = data
                return b""
            if name not in store:
                raise publish._GitLabResourceMissingError(url)
            return store[name]
        if method == "POST":
            assert data is not None
            release.update(json.loads(data))
            return b"{}"
        if release:
            return json.dumps(release).encode()
        raise publish._GitLabResourceMissingError(url)

    mocker.patch.object(publish, "_request", side_effect=request)
    assert (
        publish.publish(
            api_base="https://gitlab.example/api/v4",
            project_id=101,
            tag="v1.2.3",
            token="redacted",
            credential_kind=publish.CredentialKind.JOB_TOKEN,
            source=assets,
            trust=trust,
        )
        == "created"
    )
    assert uploads == [name for name in names if name != present]


def test_publication_rejects_different_existing_package_bytes(tmp_path: Path, mocker) -> None:
    assets, key = tmp_path / "assets", tmp_path / "signing"
    assets.mkdir()
    _assets(assets, "1.2.3")
    subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)), check=True)
    public = key.with_suffix(".pub").read_text().strip()
    trust = f'openai-responses-proxy-release namespaces="openai-responses-proxy-release" {public}'
    signing.sign_and_verify(assets=assets, key=key, trust=trust)
    conflicting = min(path.name for path in assets.iterdir())

    def request(
        url: str,
        _token: str,
        _credential_kind: publish.CredentialKind,
        *,
        data: bytes | None = None,
        method: str = "GET",
    ) -> bytes:
        del data
        if "/packages/generic/" in url:
            if method == "PUT":
                pytest.fail("publisher attempted to replace an existing asset")
            if url.endswith(f"/{conflicting}"):
                return b"different"
            raise publish._GitLabResourceMissingError(url)
        raise publish._GitLabResourceMissingError(url)

    mocker.patch.object(publish, "_request", side_effect=request)
    with pytest.raises(publish.GitLabPublishError, match="differs before upload"):
        publish.publish(
            api_base="https://gitlab.example/api/v4",
            project_id=101,
            tag="v1.2.3",
            token="redacted",
            credential_kind=publish.CredentialKind.JOB_TOKEN,
            source=assets,
            trust=trust,
        )


def test_gitlab_http_failure_preserves_bounded_provider_detail(mocker) -> None:
    error = urllib.error.HTTPError(
        "https://gitlab.example/api/v4/projects/101/releases",
        422,
        "Unprocessable Entity",
        Message(),
        io.BytesIO(b'{"message":"release validation failed"}'),
    )
    mocker.patch.object(urllib.request, "urlopen", side_effect=error)

    with pytest.raises(
        publish.GitLabPublishError,
        match=r"HTTP 422: release validation failed",
    ):
        publish._request(
            error.url,
            "redacted",
            publish.CredentialKind.PRIVATE_TOKEN,
            data=b"{}",
            method="POST",
        )


def test_publication_rejects_invalid_boundary_inputs(tmp_path: Path) -> None:
    with pytest.raises(publish.GitLabPublishError):
        publish.publish(
            api_base="file:///tmp",
            project_id=0,
            tag="latest",
            token="redacted",
            credential_kind=publish.CredentialKind.JOB_TOKEN,
            source=tmp_path,
            trust="",
        )


def test_publication_rejects_unsigned_or_incomplete_bundle(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    _assets(assets, "1.2.3")

    with pytest.raises(publish.GitLabPublishError, match="signature verification failed"):
        publish.publish(
            api_base="https://gitlab.example/api/v4",
            project_id=101,
            tag="v1.2.3",
            token="redacted",
            credential_kind=publish.CredentialKind.JOB_TOKEN,
            source=assets,
            trust="missing trust",
        )


@pytest.mark.parametrize(
    ("kind", "header"),
    [
        (publish.CredentialKind.JOB_TOKEN, "JOB-TOKEN"),
        (publish.CredentialKind.PRIVATE_TOKEN, "PRIVATE-TOKEN"),
    ],
)
def test_credential_kind_selects_one_exact_gitlab_header(
    kind: publish.CredentialKind, header: str
) -> None:
    assert publish._authentication_header(kind) == header


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (404, publish._GitLabResourceMissingError),
        (409, FileExistsError),
        (500, publish.GitLabPublishError),
    ],
)
def test_http_failure_preserves_status_and_closes_response(status, error_type, mocker):
    stream = io.BytesIO(b"provider unavailable")
    error = urllib.error.HTTPError(
        "https://gitlab.example/api", status, "failed", Message(), stream
    )
    mocker.patch.object(publish.urllib.request, "urlopen", side_effect=error)
    with pytest.raises(error_type):
        publish._request(error.url, "not-a-secret", publish.CredentialKind.JOB_TOKEN)
    assert stream.closed


@pytest.mark.parametrize("content", [b"payload", "not-bytes"])
def test_request_admits_binary_payload_and_sets_json_content_type(content, mocker):
    response = mocker.MagicMock()
    response.__enter__.return_value.read.return_value = content
    open_url = mocker.patch.object(publish.urllib.request, "urlopen", return_value=response)
    if isinstance(content, bytes):
        assert (
            publish._request(
                "https://gitlab.example/api",
                "not-a-secret",
                publish.CredentialKind.PRIVATE_TOKEN,
                data=b"{}",
                method="POST",
            )
            == content
        )
        assert open_url.call_args.args[0].get_header("Content-type") == "application/json"
    else:
        with pytest.raises(publish.GitLabPublishError, match="not binary"):
            publish._request(
                "https://gitlab.example/api", "not-a-secret", publish.CredentialKind.JOB_TOKEN
            )


def test_transport_failure_does_not_become_resource_absence(mocker):
    mocker.patch.object(publish.urllib.request, "urlopen", side_effect=OSError("offline"))
    with pytest.raises(publish.GitLabPublishError, match="transport failed"):
        publish._request(
            "https://gitlab.example/api", "not-a-secret", publish.CredentialKind.JOB_TOKEN
        )


@pytest.mark.parametrize(
    "content",
    [b"", b"a" * 3000, b'{"message":{"reason":"invalid"}}', b'"failure"', b'{"message":""}'],
)
def test_provider_diagnostic_is_bounded_for_each_payload_shape(content):
    error = urllib.error.HTTPError(
        "https://gitlab.example/api", 500, "failed", Message(), io.BytesIO(content)
    )
    try:
        detail = publish._error_detail(error)
        assert len(detail) <= 514
    finally:
        error.close()


def test_unreadable_error_body_has_no_secondary_failure(mocker):
    error = mocker.Mock()
    error.read.side_effect = OSError("closed")
    assert publish._error_detail(error) == ""


@pytest.mark.parametrize("existing", [None, {"assets": None}, {"assets": {"links": [1]}}])
def test_existing_release_requires_complete_link_objects(existing):
    expected = {"tag_name": "v1.2.3", "name": "release", "description": "notes"}
    with pytest.raises(publish.GitLabPublishError, match="immutable identity"):
        publish._require_matching_release(
            existing, expected, [{"name": "asset", "url": "url", "link_type": "package"}]
        )
