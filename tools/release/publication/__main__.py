"""Publish immutable releases and observe their exact hosted identity."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

from openai_responses_proxy import json_value
from openai_responses_proxy import product_identity
from tools.release import identity
from tools.release.publication import verification
from tools.release.publication.github import observe as github_observer
from tools.release.publication.github import publish as github
from tools.release.publication.gitlab import publish as gitlab


class PublicationError(RuntimeError):
    """One or more selected Forge publications did not complete."""


def _github(
    *,
    repository: str,
    tag: str,
    commit_oid: str,
    assets: Path,
    workspace: Path,
    checkout: Path | None = None,
) -> None:
    """Publish or verify one exact GitHub release."""
    state = github.publish(
        repository=repository,
        tag=tag,
        commit_oid=commit_oid,
        checkout=checkout or Path.cwd(),
        tag_trust=os.environ.get(product_identity.environment_name("GITHUB_TAG_TRUST"), ""),
        asset_trust=os.environ.get("RELEASE_ASSET_TRUST", ""),
        source=assets,
        workspace=workspace,
    )
    print(f"GitHub release {state}: {tag}")


def _gitlab(
    *,
    api_base: str,
    project_id: int,
    tag: str,
    assets: Path,
    credential_kind: gitlab.CredentialKind,
) -> None:
    """Publish or verify one exact GitLab release."""
    token_variable = (
        "CI_JOB_TOKEN"
        if credential_kind is gitlab.CredentialKind.JOB_TOKEN
        else product_identity.environment_name("GITLAB_PRIVATE_TOKEN")
    )
    token = os.environ.get(token_variable, "")
    trust = os.environ.get("RELEASE_ASSET_TRUST", "")
    if not token or not trust:
        raise gitlab.GitLabPublishError(
            f"GitLab {credential_kind.value} credential or release trust is unavailable"
        )
    state = gitlab.publish(
        api_base=api_base,
        project_id=project_id,
        tag=tag,
        token=token,
        credential_kind=credential_kind,
        source=assets,
        trust=trust,
    )
    print(f"GitLab release {state}: {tag}")


def _both(
    *,
    github_repository: str,
    gitlab_api_base: str,
    gitlab_project_id: int,
    tag: str,
    commit_oid: str,
    assets: Path,
    workspace: Path,
    gitlab_credential_kind: gitlab.CredentialKind,
    checkout: Path | None = None,
) -> None:
    """Publish the same immutable bundle through both independent adapters."""
    failures: list[str] = []
    try:
        _github(
            repository=github_repository,
            tag=tag,
            commit_oid=commit_oid,
            assets=assets,
            workspace=workspace,
            checkout=checkout,
        )
    except github.GitHubPublishError as error:
        failures.append(f"github: {error}")
    try:
        _gitlab(
            api_base=gitlab_api_base,
            project_id=gitlab_project_id,
            tag=tag,
            assets=assets,
            credential_kind=gitlab_credential_kind,
        )
    except gitlab.GitLabPublishError as error:
        failures.append(f"gitlab: {error}")
    if failures:
        raise PublicationError("; ".join(failures))


def _verify(
    *,
    tag: str,
    gitlab_git_url: str,
    gitlab_api_base: str,
    gitlab_repo: str,
    github_git_url: str,
    github_repo: str,
    gitlab_anchor: Path,
    github_anchor: Path,
    as_json: Annotated[bool, Parameter(name="--json", negative=False)] = False,
) -> None:
    """Verify one published tag without mutating either Forge."""
    try:
        result = verification.verify(
            tag=tag,
            gitlab_git_url=gitlab_git_url,
            gitlab_api_base=gitlab_api_base,
            gitlab_repo=gitlab_repo,
            github_git_url=github_git_url,
            github_repo=github_repo,
            gitlab_anchor=gitlab_anchor,
            github_anchor=github_anchor,
        )
    except verification.PublicationError as error:
        result = {
            "schema_version": 1,
            "tag": tag,
            "verified": False,
            "tree_equal": False,
            "reasons": list(error.reasons),
            "forges": {},
        }
    if as_json:
        print(json.dumps(json_value.thaw_value(result), sort_keys=True, separators=(",", ":")))
    else:
        print(f"publication proof: {'VERIFIED' if result['verified'] else 'UNVERIFIED'}")
    if not result["verified"]:
        raise SystemExit(1)


def _predecessor(
    *,
    repository: str,
    github_environment: Path,
    candidate_version: str | None = None,
    candidate_tag: str | None = None,
    candidate_source: Path | None = None,
) -> None:
    """Print the exact published predecessor for one candidate identity."""
    if (
        sum(value is not None for value in (candidate_version, candidate_tag, candidate_source))
        != 1
    ):
        raise ValueError("provide exactly one candidate version, tag or source")
    if candidate_source is not None:
        try:
            candidate_version = (candidate_source / "VERSION").read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError("candidate source VERSION is unavailable") from error
        if not identity.is_version(candidate_version):
            raise ValueError("candidate source VERSION is not a release SemVer")
    version = (
        candidate_version
        if candidate_version is not None
        else identity.version_from_tag(candidate_tag or "")
    )
    tag = github_observer.published_predecessor(repository=repository, version=version)
    with github_environment.open("a", encoding="utf-8", newline="\n") as environment:
        environment.write(f"{product_identity.environment_name('PREVIOUS_RELEASE_TAG')}={tag}\n")
        predecessor_product = (
            "codex-responses-proxy"
            if int(identity.version_from_tag(tag).split(".")[0]) < 5
            else product_identity.PRODUCT_SLUG
        )
        environment.write(
            f"{product_identity.environment_name('PREVIOUS_RELEASE_PRODUCT')}={predecessor_product}\n"
        )
    print(tag)


def _app() -> App:
    app = App(
        name=f"python -m {__package__}",
        help=__doc__,
        print_error=False,
        exit_on_error=False,
        result_action="return_value",
    )
    app.command(_github, name="github")
    app.command(_gitlab, name="gitlab")
    app.command(_both, name="both")
    app.command(_verify, name="verify")
    app.command(_predecessor, name="predecessor")
    return app


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run publication effects and observations from one explicit command tree."""
    try:
        _app()(tuple(sys.argv[1:] if argv is None else argv))
    except (
        github.GitHubPublishError,
        gitlab.GitLabPublishError,
        github_observer.GitHubProofError,
        PublicationError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
