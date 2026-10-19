"""Provider-neutral release publication command contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.release.publication import __main__ as commands
from tools.release.publication.github import observe as github_observer
from tools.release.publication.github import publish as github
from tools.release.publication.gitlab import publish as gitlab


def test_github_command_dispatches_verified_inputs_without_secret_arguments(
    tmp_path: Path, mocker
) -> None:
    adapter = mocker.patch.object(github, "publish", return_value="created")
    mocker.patch.dict(
        commands.os.environ,
        {
            "OPENAI_RESPONSES_PROXY_GITHUB_TAG_TRUST": "tag trust",
            "RELEASE_ASSET_TRUST": "asset trust",
        },
        clear=True,
    )

    commands.main(
        (
            "github",
            "--repository",
            "team/proxy",
            "--tag",
            "v1.2.3",
            "--commit-oid",
            "a" * 40,
            "--assets",
            str(tmp_path / "assets"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--checkout",
            str(tmp_path),
        )
    )

    adapter.assert_called_once_with(
        repository="team/proxy",
        tag="v1.2.3",
        commit_oid="a" * 40,
        checkout=tmp_path,
        tag_trust="tag trust",
        asset_trust="asset trust",
        source=tmp_path / "assets",
        workspace=tmp_path / "workspace",
    )


@pytest.mark.parametrize(
    ("kind", "variable", "token"),
    [
        (gitlab.CredentialKind.JOB_TOKEN, "CI_JOB_TOKEN", "job token"),
        (
            gitlab.CredentialKind.PRIVATE_TOKEN,
            "OPENAI_RESPONSES_PROXY_GITLAB_PRIVATE_TOKEN",
            "private token",
        ),
    ],
)
def test_gitlab_command_reads_only_the_declared_credential(
    tmp_path: Path, mocker, kind: gitlab.CredentialKind, variable: str, token: str
) -> None:
    adapter = mocker.patch.object(gitlab, "publish", return_value="matched")
    mocker.patch.dict(
        commands.os.environ,
        {
            "CI_JOB_TOKEN": "unused job token",
            "OPENAI_RESPONSES_PROXY_GITLAB_PRIVATE_TOKEN": "unused private token",
            variable: token,
            "RELEASE_ASSET_TRUST": "asset trust",
        },
        clear=True,
    )

    commands.main(
        (
            "gitlab",
            "--api-base",
            "https://gitlab.example/api/v4",
            "--project-id",
            "453",
            "--tag",
            "v1.2.3",
            "--assets",
            str(tmp_path / "assets"),
            "--credential-kind",
            kind.value,
        )
    )

    adapter.assert_called_once_with(
        api_base="https://gitlab.example/api/v4",
        project_id=453,
        tag="v1.2.3",
        token=token,
        credential_kind=kind,
        source=tmp_path / "assets",
        trust="asset trust",
    )


def test_command_fails_closed_without_provider_credentials(tmp_path: Path, mocker) -> None:
    mocker.patch.dict(commands.os.environ, {}, clear=True)

    with pytest.raises(SystemExit) as failure:
        commands.main(
            (
                "gitlab",
                "--api-base",
                "https://gitlab.example/api/v4",
                "--project-id",
                "453",
                "--tag",
                "v1.2.3",
                "--assets",
                str(tmp_path),
                "--credential-kind",
                "job-token",
            )
        )

    assert failure.value.code == 1


def test_publication_commands_share_one_semantic_entrypoint(capsys) -> None:
    commands.main(("--help",))
    help_text = capsys.readouterr().out
    assert f"python -m {commands.__package__}" in help_text
    for operation in ("github", "gitlab", "both", "verify", "predecessor"):
        assert operation in help_text


def test_dual_command_projects_the_same_bundle_to_both_peers(tmp_path: Path, mocker) -> None:
    github_dispatch = mocker.patch.object(commands, "_github")
    gitlab_dispatch = mocker.patch.object(commands, "_gitlab")
    assets = tmp_path / "assets"
    workspace = tmp_path / "workspace"

    commands._both(
        github_repository="team/proxy",
        gitlab_api_base="https://gitlab.example/api/v4",
        gitlab_project_id=453,
        tag="v1.2.3",
        commit_oid="a" * 40,
        assets=assets,
        workspace=workspace,
        gitlab_credential_kind=gitlab.CredentialKind.JOB_TOKEN,
        checkout=tmp_path,
    )

    github_dispatch.assert_called_once_with(
        repository="team/proxy",
        tag="v1.2.3",
        commit_oid="a" * 40,
        assets=assets,
        workspace=workspace,
        checkout=tmp_path,
    )
    gitlab_dispatch.assert_called_once_with(
        api_base="https://gitlab.example/api/v4",
        project_id=453,
        tag="v1.2.3",
        assets=assets,
        credential_kind=gitlab.CredentialKind.JOB_TOKEN,
    )


def test_dual_command_attempts_both_peers_before_reporting_failure(tmp_path: Path, mocker) -> None:
    mocker.patch.object(
        commands,
        "_github",
        side_effect=github.GitHubPublishError("github unavailable"),
    )
    gitlab_dispatch = mocker.patch.object(commands, "_gitlab")

    with pytest.raises(commands.PublicationError, match="github unavailable"):
        commands._both(
            github_repository="team/proxy",
            gitlab_api_base="https://gitlab.example/api/v4",
            gitlab_project_id=453,
            tag="v1.2.3",
            commit_oid="a" * 40,
            assets=tmp_path / "assets",
            workspace=tmp_path / "workspace",
            gitlab_credential_kind=gitlab.CredentialKind.JOB_TOKEN,
        )

    gitlab_dispatch.assert_called_once()


def test_github_predecessor_cli_projects_the_exact_tag_to_github_environment(
    *, mocker, capsys, tmp_path
) -> None:
    """Project the exact tag without depending on the runner's command shell."""
    resolve = mocker.patch.object(github_observer, "published_predecessor", return_value="v3.1.0")
    environment = tmp_path / "github-environment"

    commands.main(
        (
            "predecessor",
            "--repository",
            "owner/repo",
            "--candidate-version",
            "3.1.2",
            "--github-environment",
            str(environment),
        )
    )

    resolve.assert_called_once_with(repository="owner/repo", version="3.1.2")
    assert capsys.readouterr().out == "v3.1.0\n"
    assert environment.read_text(encoding="utf-8") == (
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG=v3.1.0\n"
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT=codex-responses-proxy\n"
    )


def test_github_predecessor_cli_accepts_the_release_tag_as_identity(
    *, mocker, capsys, tmp_path
) -> None:
    """Derive the candidate version without runner-shell command substitution."""
    resolve = mocker.patch.object(github_observer, "published_predecessor", return_value="v3.1.0")
    environment = tmp_path / "github-environment"

    commands.main(
        (
            "predecessor",
            "--repository",
            "owner/repo",
            "--candidate-tag",
            "v3.1.2",
            "--github-environment",
            str(environment),
        )
    )

    resolve.assert_called_once_with(repository="owner/repo", version="3.1.2")
    assert capsys.readouterr().out == "v3.1.0\n"
    assert environment.read_text(encoding="utf-8") == (
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG=v3.1.0\n"
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT=codex-responses-proxy\n"
    )


def test_github_predecessor_cli_reads_the_candidate_source_identity(
    *, mocker, capsys, tmp_path
) -> None:
    resolve = mocker.patch.object(github_observer, "published_predecessor", return_value="v5.0.5")
    source = tmp_path / "candidate source"
    source.mkdir()
    (source / "VERSION").write_text("5.0.6\n", encoding="utf-8")
    environment = tmp_path / "github-environment"

    commands.main(
        (
            "predecessor",
            "--repository",
            "owner/repo",
            "--candidate-source",
            str(source),
            "--github-environment",
            str(environment),
        )
    )

    resolve.assert_called_once_with(repository="owner/repo", version="5.0.6")
    assert capsys.readouterr().out == "v5.0.5\n"
    assert environment.read_text(encoding="utf-8") == (
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG=v5.0.5\n"
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT=openai-responses-proxy\n"
    )


@pytest.mark.parametrize("version", [None, "latest"])
def test_github_predecessor_cli_refuses_unproved_source_before_remote_lookup(
    tmp_path, mocker, version
) -> None:
    resolve = mocker.patch.object(github_observer, "published_predecessor")
    source = tmp_path / "source"
    source.mkdir()
    if version is not None:
        (source / "VERSION").write_text(version, encoding="utf-8")

    with pytest.raises(SystemExit):
        commands.main(
            (
                "predecessor",
                "--repository",
                "owner/repo",
                "--candidate-source",
                str(source),
                "--github-environment",
                str(tmp_path / "github-environment"),
            )
        )

    resolve.assert_not_called()
    assert not (tmp_path / "github-environment").exists()
