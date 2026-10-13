"""Live dual-Forge publication verification.

The evaluator and provider adapters produce immutable, secret-free evidence for
release governance.  Installation has a separate signed-source trust boundary
and never consumes this evidence as authority.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path

from openai_responses_proxy.serving import digest
from tools.release import identity
from tools.release.publication import evaluator
from tools.release.publication import git
from tools.release.publication.github import observe as github
from tools.release.publication.gitlab import observe as gitlab


class PublicationError(RuntimeError):
    """Report unavailable, ambiguous, or mismatched publication evidence."""

    def __init__(self, message: str, *, reasons: Sequence[str]) -> None:
        """Retain stable secret-free reasons beside the human message."""
        super().__init__(message)
        self.reasons = tuple(reasons)


def _git_evidence(provider: str, git_url: str, tag: str, anchor: Path) -> Mapping[str, object]:
    """Collect one Forge's exact Git identity with a provider-scoped error."""
    try:
        return git.collect(provider=provider, remote=git_url, tag=tag, anchor=anchor)
    except (git.GitProofError, OSError) as error:
        raise PublicationError(
            "live publication Git evidence is unavailable or invalid",
            reasons=(f"{provider}.remote_git_evidence_invalid",),
        ) from error


def _hosted_evidence(
    provider: str,
    collect: Callable[..., Mapping[str, object]],
    **arguments: object,
) -> Mapping[str, object]:
    """Collect one Forge's hosted evidence with a provider-scoped error."""
    try:
        return collect(**arguments)
    except (gitlab.GitLabProofError, github.GitHubProofError, OSError) as error:
        raise PublicationError(
            "live hosted publication evidence is unavailable or invalid",
            reasons=(f"{provider}.hosted_evidence_invalid",),
        ) from error


def _evaluation_evidence(
    git_evidence: Mapping[str, object],
    hosted_evidence: Mapping[str, object],
) -> dict[str, object]:
    """Project validated provider evidence into the evaluator's closed schema."""
    ci = hosted_evidence.get("ci")
    if not isinstance(ci, Mapping):
        raise PublicationError(
            "hosted publication CI evidence is malformed",
            reasons=("hosted_ci_evidence_malformed",),
        )
    return {
        **git_evidence,
        "repository": hosted_evidence.get("repository"),
        "assets": hosted_evidence.get("assets"),
        "ci": {
            "id": ci.get("id"),
            "revision_oid": ci.get("revision_oid"),
            "status": ci.get("status"),
        },
        "release": hosted_evidence.get("release"),
    }


def verify(
    *,
    tag: str,
    gitlab_git_url: str,
    gitlab_api_base: str,
    gitlab_repo: str,
    github_git_url: str,
    github_repo: str,
    gitlab_anchor: Path,
    github_anchor: Path,
) -> Mapping[str, object]:
    """Return immutable evidence for exact signed releases on both Forges."""
    if not identity.is_tag(tag):
        raise PublicationError(
            "publication tag must be exact vMAJOR.MINOR.PATCH",
            reasons=("release_tag_invalid",),
        )
    gitlab_git = _git_evidence("gitlab", gitlab_git_url, tag, gitlab_anchor)
    github_git = _git_evidence("github", github_git_url, tag, github_anchor)
    try:
        gitlab_hosted = _hosted_evidence(
            "gitlab",
            gitlab.collect,
            api_base=gitlab_api_base,
            repository=gitlab_repo,
            tag=tag,
            tag_object_oid=str(gitlab_git["tag_object_oid"]),
            commit_oid=str(gitlab_git["commit_oid"]),
        )
        github_hosted = _hosted_evidence(
            "github",
            github.collect,
            repository=github_repo,
            tag=tag,
            tag_object_oid=str(github_git["tag_object_oid"]),
            commit_oid=str(github_git["commit_oid"]),
        )
        result = evaluator.evaluate(
            tag,
            _evaluation_evidence(gitlab_git, gitlab_hosted),
            _evaluation_evidence(github_git, github_hosted),
        )
    except (ValueError, KeyError) as error:
        raise PublicationError(
            "publication evidence is malformed",
            reasons=("publication_evidence_schema_invalid",),
        ) from error
    if result.get("verified") is not True:
        reasons = result.get("reasons")
        stable_reasons = (
            tuple(reasons)
            if isinstance(reasons, list)
            and reasons
            and all(isinstance(item, str) for item in reasons)
            else ("dual_forge_publication_unverified",)
        )
        raise PublicationError(
            "dual-Forge publication proof did not verify",
            reasons=stable_reasons,
        )
    forges = result.get("forges")
    if not isinstance(forges, Mapping):
        raise PublicationError(
            "verified publication evidence is malformed",
            reasons=("verified_publication_evidence_malformed",),
        )
    if result.get("assets_equal") is not True:
        raise PublicationError(
            "dual-Forge release assets differ",
            reasons=("release_assets_differ",),
        )
    return digest.freeze_mapping(
        {
            "schema_version": 1,
            "tag": tag,
            "verified": True,
            "tree_equal": True,
            "assets_equal": True,
            "forges": dict(forges),
        }
    )
