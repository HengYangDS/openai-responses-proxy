"""GitHub-native release publication contracts."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from typing import Literal

import pytest

from tools.release.publication.github import publish

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _release_checkout(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    subprocess.run(("git", "init", "-q", "-b", "dev", str(repository)), check=True)
    _git(repository, "config", "core.hooksPath", "/dev/null")
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release@example.test")
    (repository / "tracked.txt").write_text("accepted\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "test: accepted source")
    commit_oid = _git(repository, "rev-parse", "HEAD")
    _git(repository, "tag", "-a", "v1.2.3", "-m", "release v1.2.3")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "tracked.txt").write_text("working\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    return repository, commit_oid


def _checkout_state(repository: Path) -> tuple[str, ...]:
    return (
        _git(repository, "symbolic-ref", "HEAD"),
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "status", "--porcelain=v1"),
        _git(repository, "diff", "--cached", "--binary"),
        _git(repository, "diff", "--binary"),
        (repository / "untracked.txt").read_text(encoding="utf-8"),
    )


def test_release_record_selection_is_exact_and_fail_closed(subtests: pytest.Subtests) -> None:
    matching = {
        "id": 7,
        "tag_name": "v1.2.3",
        "name": "OpenAI Responses Proxy v1.2.3",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-09T00:00:00Z",
    }
    assert publish.select_release([matching], "v1.2.3") == matching
    assert publish.select_release([], "v1.2.3") is None
    for records in (
        [matching, matching],
        [{**matching, "name": "wrong"}],
        [{**matching, "published_at": None}],
    ):
        with (
            subtests.test(records=records),
            pytest.raises(publish.GitHubPublishError),
        ):
            publish.select_release(records, "v1.2.3")


def test_remote_annotated_tag_is_bound_to_local_objects() -> None:
    tag_oid, commit_oid = "b" * 40, "a" * 40
    publish.verify_remote_tag(
        ref={"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": tag_oid}},
        tag_object={
            "tag": "v1.2.3",
            "sha": tag_oid,
            "object": {"type": "commit", "sha": commit_oid},
        },
        tag="v1.2.3",
        tag_oid=tag_oid,
        commit_oid=commit_oid,
    )
    with pytest.raises(publish.GitHubPublishError):
        publish.verify_remote_tag(
            ref={
                "ref": "refs/tags/v1.2.3",
                "object": {"type": "commit", "sha": tag_oid},
            },
            tag_object={},
            tag="v1.2.3",
            tag_oid=tag_oid,
            commit_oid=commit_oid,
        )


@pytest.mark.parametrize("state", ["created", "matched", "digest-drift", "byte-drift"])
def test_publish_owns_download_validation_creation_and_byte_parity(
    state: Literal["created", "matched", "digest-drift", "byte-drift"],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    source, downloaded = tmp_path / "source", tmp_path / "downloaded"
    source.mkdir()
    downloaded.mkdir()
    (source / "SHA256SUMS").write_bytes(b"checksums")
    (source / "SHA256SUMS.sig").write_bytes(b"signature")
    (downloaded / "SHA256SUMS").write_bytes(b"checksums")
    (downloaded / "SHA256SUMS.sig").write_bytes(b"signature")
    mocker.patch.object(publish, "_local_tag_identity", return_value=("b" * 40, "a" * 40))
    mocker.patch.object(publish, "_verify_source")
    mocker.patch.object(publish, "_verify_remote_identity")
    existing = {
        "id": 7,
        "tag_name": "v1.2.3",
        "name": "OpenAI Responses Proxy v1.2.3",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-09T00:00:00Z",
    }
    mocker.patch.object(
        publish, "_release_records", return_value=[existing] if state == "matched" else []
    )
    downloaded_digest = "changed" if state == "digest-drift" else "1"
    if state == "byte-drift":
        (downloaded / "SHA256SUMS").write_bytes(b"changed")
    mocker.patch.object(
        publish,
        "_verify_assets",
        side_effect=(
            {"SHA256SUMS": "1", "SHA256SUMS.sig": "2"},
            {"SHA256SUMS": downloaded_digest, "SHA256SUMS.sig": "2"},
        ),
    )
    create = mocker.patch.object(publish, "_create_release")
    mocker.patch.object(publish, "_download_release_assets", return_value=downloaded)

    def execute():
        return publish.publish(
            repository="team/proxy",
            tag="v1.2.3",
            commit_oid="a" * 40,
            checkout=tmp_path,
            tag_trust="tag trust",
            asset_trust="asset trust",
            source=source,
            workspace=tmp_path / "workspace",
        )

    if state in {"digest-drift", "byte-drift"}:
        with pytest.raises(publish.GitHubPublishError, match="differ after publication"):
            execute()
    else:
        assert execute() == state
    assert create.call_count == (0 if state == "matched" else 1)
    assert not hasattr(publish, "wait_for_verify")


def test_local_tag_identity_reads_exact_objects(tmp_path: Path, mocker: MockerFixture) -> None:
    output = mocker.patch.object(
        publish,
        "_output",
        side_effect=("tag", "b" * 40, "a" * 40),
    )

    assert publish._local_tag_identity(tmp_path, "v1.2.3", "a" * 40) == (
        "b" * 40,
        "a" * 40,
    )
    assert output.call_count == 3


def test_release_checkout_verification_preserves_repository_state(tmp_path: Path) -> None:
    repository, commit_oid = _release_checkout(tmp_path)
    before = _checkout_state(repository)

    assert publish._local_tag_identity(repository, "v1.2.3", commit_oid)[1] == commit_oid

    assert _checkout_state(repository) == before


def test_release_checkout_mismatch_fails_without_repository_state_change(tmp_path: Path) -> None:
    repository, _ = _release_checkout(tmp_path)
    before = _checkout_state(repository)

    with pytest.raises(
        publish.GitHubPublishError,
        match="release tag differs from the verified commit",
    ):
        publish._local_tag_identity(repository, "v1.2.3", "0" * 40)

    assert _checkout_state(repository) == before


def test_release_validation_preserves_the_active_environment(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Do not resolve a virtual-environment interpreter into the host Python."""
    host_python = tmp_path / "host-python"
    host_python.write_text("", encoding="utf-8")
    environment_python = tmp_path / "environment-python"
    environment_python.symlink_to(host_python)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    mocker.patch.object(publish.sys, "executable", str(environment_python))
    mocker.patch.object(publish.tag_signature, "verify")
    run = mocker.patch.object(publish, "_run")

    publish._verify_source(checkout, "v1.2.3", "trust")

    assert run.call_count == 1
    assert all(call.args[0][0] == str(environment_python) for call in run.call_args_list)


@pytest.mark.parametrize("pages", [None, [None], [[None]], [[{"tag_name": "v1.2.3"}], []]])
def test_release_inventory_reads_all_pages_and_requires_records(
    pages, mocker: MockerFixture
) -> None:
    mocker.patch.object(publish.hosted, "executable", return_value="gh")
    api = mocker.patch.object(publish.hosted, "api_json", return_value=pages)
    if pages == [[{"tag_name": "v1.2.3"}], []]:
        assert publish._release_records("team/proxy") == [{"tag_name": "v1.2.3"}]
    else:
        with pytest.raises(publish.GitHubPublishError, match="records are malformed"):
            publish._release_records("team/proxy")
    assert "--paginate" in api.call_args.args[0]
    assert "--slurp" in api.call_args.args[0]


@pytest.mark.parametrize("value", [None, {1: "invalid"}, {"sha": "a" * 40}])
def test_identity_api_requires_string_keyed_objects(value, mocker: MockerFixture) -> None:
    mocker.patch.object(publish.hosted, "api_json", return_value=value)
    if value == {"sha": "a" * 40}:
        assert publish._api_mapping(("gh", "api")) == value
    else:
        with pytest.raises(publish.GitHubPublishError, match="identity is malformed"):
            publish._api_mapping(("gh", "api"))


def test_remote_identity_reads_both_exact_annotated_objects(mocker: MockerFixture) -> None:
    tag_oid, commit_oid = "b" * 40, "a" * 40
    mocker.patch.object(publish.hosted, "executable", return_value="gh")
    api = mocker.patch.object(
        publish.hosted,
        "api_json",
        side_effect=[
            {"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": tag_oid}},
            {"tag": "v1.2.3", "sha": tag_oid, "object": {"type": "commit", "sha": commit_oid}},
        ],
    )
    publish._verify_remote_identity("team/proxy", "v1.2.3", tag_oid, commit_oid)
    assert [call.args[0][-1] for call in api.call_args_list] == [
        "repos/team/proxy/git/ref/tags/v1.2.3",
        f"repos/team/proxy/git/tags/{tag_oid}",
    ]


@pytest.mark.parametrize("failure", [OSError("missing"), ValueError("invalid")])
def test_asset_validation_preserves_bounded_error(
    tmp_path: Path, failure, mocker: MockerFixture
) -> None:
    mocker.patch.object(publish.assembly, "verify", side_effect=failure)
    with pytest.raises(publish.GitHubPublishError, match="assets are invalid"):
        publish._verify_assets(tmp_path, "trust")


def test_asset_validation_passes_external_trust_unchanged(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    verify = mocker.patch.object(publish.assembly, "verify", return_value={"asset": "digest"})
    assert publish._verify_assets(tmp_path, "external trust") == {"asset": "digest"}
    verify.assert_called_once_with(tmp_path, trust="external trust")


def test_release_creation_and_download_have_explicit_asset_scope(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(publish.hosted, "executable", return_value="gh")
    run = mocker.patch.object(publish.subprocess, "run")
    with pytest.raises(publish.GitHubPublishError, match="asset set is empty"):
        publish._create_release("team/proxy", "v1.2.3", tmp_path)
    run.assert_not_called()
    asset = tmp_path / "SHA256SUMS"
    asset.write_bytes(b"digest")
    publish._create_release("team/proxy", "v1.2.3", tmp_path)
    command = run.call_args.args[0]
    assert command[:4] == ("gh", "release", "create", "v1.2.3")
    assert "--verify-tag" in command
    assert command[-1] == str(asset)
    target = tmp_path / "download"
    assert publish._download_release_assets("team/proxy", "v1.2.3", target) == target
    command = run.call_args.args[0]
    assert command[:4] == ("gh", "release", "download", "v1.2.3")
    assert "SHA256SUMS*" in command
    assert target.is_dir()


@pytest.mark.parametrize("failure", [OSError("offline"), subprocess.CalledProcessError(1, ["git"])])
def test_transport_failures_are_translated_once(failure, mocker: MockerFixture) -> None:
    run = mocker.patch.object(publish.subprocess, "run", side_effect=failure)
    with pytest.raises(publish.GitHubPublishError, match="publication unavailable"):
        publish._run(("gh", "release"), "publication unavailable")
    with pytest.raises(publish.GitHubPublishError, match="Git identity is unavailable"):
        publish._output(("git", "rev-parse"))
    assert run.call_count == 2


def test_invalid_source_signature_never_runs_metadata(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(
        publish.tag_signature,
        "verify",
        side_effect=publish.tag_signature.TagSignatureError("invalid"),
    )
    run = mocker.patch.object(publish, "_run")
    with pytest.raises(publish.GitHubPublishError, match="signature is invalid"):
        publish._verify_source(tmp_path, "v1.2.3", "trust")
    run.assert_not_called()


def test_lightweight_tag_is_not_an_annotated_release(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(publish, "_output", return_value="commit")
    with pytest.raises(publish.GitHubPublishError, match="not annotated"):
        publish._local_tag_identity(tmp_path, "v1.2.3", "a" * 40)


def test_invalid_publication_never_creates_a_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with pytest.raises(publish.GitHubPublishError, match="inputs are invalid"):
        publish.publish(
            repository="",
            tag="v1.2.3",
            commit_oid="a" * 40,
            checkout=tmp_path,
            tag_trust="trust",
            asset_trust="trust",
            source=tmp_path,
            workspace=workspace,
        )
    assert not workspace.exists()
