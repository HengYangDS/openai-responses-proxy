"""Pure contracts for dual-Forge publication proof evaluation."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import cast

import pytest

from openai_responses_proxy.lifecycle import artifact
from tests.release.publication.fixtures import VERIFY_ARGUMENTS
from tests.release.publication.fixtures import VerifyArguments
from tests.release.publication.fixtures import forge_evidence
from tests.release.publication.fixtures import verified_evidence
from tools.release.artifact import format as product_assets
from tools.release.publication import evaluator
from tools.release.publication import verification as publication

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _forge(
    provider: str,
    *,
    repository: str,
    tag_object_oid: str,
    commit_oid: str,
    tree: str = "c" * 40,
) -> dict[str, object]:
    return {
        "provider": provider,
        "repository": repository,
        "tag": "v1.2.3",
        "tag_object_oid": tag_object_oid,
        "commit_oid": commit_oid,
        "tree_oid": tree,
        "anchor_sha256": "f" * 64,
        "signature_verified": True,
        "assets": {
            **{
                f"openai-responses-proxy-1.2.3-{platform}.tar.gz": "1" * 64
                for platform in ("linux-x86_64", "macos-arm64", "windows-x86_64")
            },
            **{
                f"openai-responses-proxy-{platform}.manifest.json": "2" * 64
                for platform in ("linux-x86_64", "macos-arm64", "windows-x86_64")
            },
            "SHA256SUMS": "3" * 64,
            "SHA256SUMS.sig": "4" * 64,
        },
        "ci": {
            "id": 42,
            "revision_oid": commit_oid,
            "status": "success",
        },
        "release": {
            "id": 99,
            "tag": "v1.2.3",
            "commit_oid": commit_oid,
            "name": "OpenAI Responses Proxy v1.2.3",
            "draft": False,
            "prerelease": False,
        },
    }


def _gitlab_forge(*, tree: str = "c" * 40) -> dict[str, object]:
    return _forge(
        "gitlab",
        repository="example/gitlab",
        tag_object_oid="a" * 40,
        commit_oid="d" * 40,
        tree=tree,
    )


def _github_forge(*, tree: str = "c" * 40) -> dict[str, object]:
    return _forge(
        "github",
        repository="example/github",
        tag_object_oid="a" * 40,
        commit_oid="d" * 40,
        tree=tree,
    )


class PublicationProofContracts:
    """Admit only complete independent publications of one source tree."""

    def test_valid_proof_remains_evidence_only(self) -> None:
        result = evaluator.evaluate("v1.2.3", _gitlab_forge(), _github_forge())
        assert result["verified"]
        assert result["tree_equal"]
        assert result["assets_equal"]
        assert "release_source" not in result
        forges = cast("dict[str, dict[str, object]]", result["forges"])
        assert forges["gitlab"]["provider"] == "gitlab"
        assert forges["github"]["provider"] == "github"
        assert "jobs" not in cast("dict[str, object]", forges["gitlab"]["ci"])
        assert "jobs" not in cast("dict[str, object]", forges["github"]["ci"])
        assert result["reasons"] == []

    def test_unknown_fields_fail_closed(self) -> None:
        gitlab = _gitlab_forge()
        gitlab["unexpected"] = "value"

        result = evaluator.evaluate("v1.2.3", gitlab, _github_forge())

        assert not result["verified"]
        assert result["reasons"] == ["gitlab.invalid_evidence"]

    def test_rejects_tree_mismatch_and_unverified_signature(self) -> None:
        gitlab = _gitlab_forge()
        github = _github_forge(tree="9" * 40)
        github["signature_verified"] = False
        result = evaluator.evaluate("v1.2.3", gitlab, github)
        assert not result["verified"]
        assert not result["tree_equal"]
        reasons = cast("list[str]", result["reasons"])
        assert "tree_mismatch" in reasons
        assert "github.signature_unverified" in reasons

    def test_rejects_cross_forge_git_identity_mismatch(self) -> None:
        github = _github_forge()
        github["tag_object_oid"] = "b" * 40
        github["commit_oid"] = "e" * 40
        cast("dict[str, object]", github["ci"])["revision_oid"] = "e" * 40
        cast("dict[str, object]", github["release"])["commit_oid"] = "e" * 40

        result = evaluator.evaluate("v1.2.3", _gitlab_forge(), github)

        assert not result["verified"]
        reasons = cast("list[str]", result["reasons"])
        assert "tag_object_mismatch" in reasons
        assert "commit_mismatch" in reasons

    def test_rejects_cross_forge_asset_mismatch(self) -> None:
        github = _github_forge()
        cast("dict[str, str]", github["assets"])[
            "openai-responses-proxy-1.2.3-linux-x86_64.tar.gz"
        ] = "9" * 64
        result = evaluator.evaluate("v1.2.3", _gitlab_forge(), github)
        assert not result["verified"]
        assert not result["assets_equal"]
        assert "asset_mismatch" in cast("list[str]", result["reasons"])

    def test_rejects_incomplete_forge_asset_inventory(self) -> None:
        gitlab = _gitlab_forge()
        assets = cast("dict[str, str]", gitlab["assets"])
        for platform in ("macos-arm64", "windows-x86_64"):
            assets.pop(f"openai-responses-proxy-1.2.3-{platform}.tar.gz")
            assets.pop(f"openai-responses-proxy-{platform}.manifest.json")

        result = evaluator.evaluate("v1.2.3", gitlab, _github_forge())

        assert not result["verified"]
        assert "gitlab.invalid_evidence" in cast("list[str]", result["reasons"])

    def test_rejects_checksum_signature_and_trust_identity_mismatch(self) -> None:
        github = _github_forge()
        assets = cast("dict[str, str]", github["assets"])
        assets["SHA256SUMS"] = "8" * 64
        assets["SHA256SUMS.sig"] = "9" * 64
        github["anchor_sha256"] = "7" * 64

        result = evaluator.evaluate("v1.2.3", _gitlab_forge(), github)

        assert not result["verified"]
        reasons = cast("list[str]", result["reasons"])
        assert "asset_mismatch" in reasons
        assert "trust_anchor_mismatch" in reasons

    def test_rejects_wrong_ci_revision_and_release_identity(self) -> None:
        gitlab = _gitlab_forge()
        ci = gitlab["ci"]
        assert isinstance(ci, dict)
        typed_ci = cast("dict[str, object]", ci)
        typed_ci["revision_oid"] = "0" * 40
        release = gitlab["release"]
        assert isinstance(release, dict)
        cast("dict[str, object]", release)["draft"] = True
        result = evaluator.evaluate("v1.2.3", gitlab, _github_forge())
        assert not result["verified"]
        reasons = cast("list[str]", result["reasons"])
        assert "gitlab.ci_revision_mismatch" in reasons
        assert "gitlab.release_draft" in reasons

    def test_invalid_shapes_and_all_semantic_mismatches_fail_closed(
        self, subtests: pytest.Subtests
    ) -> None:
        invalid = {
            "tag": "latest",
            "repository": "",
            "tag_object_oid": "not-an-oid",
            "anchor_sha256": "not-a-digest",
            "assets": {},
            "ci": [],
        }
        for field, value in invalid.items():
            evidence = _gitlab_forge()
            evidence[field] = value
            with subtests.test(field=field):
                assert evaluator._evaluate_forge("gitlab", "v1.2.3", evidence)[0] is None

        evidence = _gitlab_forge()
        cast("dict[str, object]", evidence["assets"])["SHA256SUMS"] = "not-a-digest"
        assert evaluator._evaluate_forge("gitlab", "v1.2.3", evidence)[0] is None

        evidence = _gitlab_forge()
        cast("dict[str, object]", evidence["release"]).pop("name")
        assert evaluator._evaluate_forge("gitlab", "v1.2.3", evidence)[0] is None

        evidence = _gitlab_forge()
        ci = cast("dict[str, object]", evidence["ci"])
        release = cast("dict[str, object]", evidence["release"])
        ci.update(status="failed")
        release.update(
            tag="v0.0.0",
            commit_oid="0" * 40,
            name="wrong",
            draft=True,
            prerelease=True,
        )
        result = evaluator.evaluate("v1.2.3", evidence, _github_forge())
        reasons = cast("list[str]", result["reasons"])
        for expected in (
            "gitlab.ci_not_successful",
            "gitlab.release_tag_mismatch",
            "gitlab.release_commit_mismatch",
            "gitlab.release_name_mismatch",
            "gitlab.release_prerelease",
        ):
            assert expected in reasons


class PublicationEvidenceContracts:
    """Verify immutable, secret-free publication evidence without installation authority."""

    def test_publication_owner_imports_semantic_collaborators(self) -> None:
        assert publication.evaluator is evaluator

    def test_evidence_projection_has_no_installation_capability(
        self, *, mocker: MockerFixture
    ) -> None:
        assert not hasattr(artifact, "ForgePublication")
        assert not hasattr(artifact, "PublicationProof")
        assert not hasattr(publication, "PublishedRelease")
        assert not hasattr(publication, "consume")
        assert verified_evidence(forge_evidence(), mocker=mocker)["verified"]

    def test_publication_verifier_fails_closed_on_invalid_inputs(
        self, *, mocker: MockerFixture
    ) -> None:
        arguments = VERIFY_ARGUMENTS
        mocker.patch.object(
            publication.git,
            "collect",
            side_effect=publication.git.GitProofError("offline"),
        )
        with pytest.raises(publication.PublicationError, match="unavailable or invalid") as failure:
            publication.verify(**arguments)
        assert failure.value.reasons == ("gitlab.remote_git_evidence_invalid",)

        invalid_tag: VerifyArguments = {**arguments, "tag": "latest"}
        with pytest.raises(publication.PublicationError, match="exact vMAJOR") as failure:
            publication.verify(**invalid_tag)
        assert failure.value.reasons == ("release_tag_invalid",)

    def test_publication_verifier_composes_validated_adapter_evidence(
        self, *, mocker: MockerFixture
    ) -> None:
        commit = "b" * 40
        git_evidence = {
            "tag_object_oid": "a" * 40,
            "commit_oid": commit,
            "tree_oid": "c" * 40,
            "anchor_sha256": "f" * 64,
            "signature_verified": True,
        }
        assets = dict.fromkeys(
            product_assets.release_asset_names("1.2.3", product_assets.RELEASE_PLATFORMS),
            "1" * 64,
        )

        def collect_git(*, provider: str, **_: object) -> dict[str, object]:
            return {"provider": provider, "tag": "v1.2.3", **git_evidence}

        def collect_hosted(*, repository: str, **_: object) -> dict[str, object]:
            return {
                "repository": repository,
                "ci": {
                    "id": 42,
                    "revision_oid": commit,
                    "status": "success",
                    "jobs": {"required": "success"},
                },
                "release": {
                    "id": 99,
                    "tag": "v1.2.3",
                    "commit_oid": commit,
                    "name": "OpenAI Responses Proxy v1.2.3",
                    "draft": False,
                    "prerelease": False,
                },
                "assets": assets,
            }

        mocker.patch.object(publication.git, "collect", side_effect=collect_git)
        mocker.patch.object(publication.gitlab, "collect", side_effect=collect_hosted)
        mocker.patch.object(publication.github, "collect", side_effect=collect_hosted)

        result = publication.verify(**VERIFY_ARGUMENTS)

        assert result["verified"] is True
        forges = result["forges"]
        assert isinstance(forges, Mapping)
        for forge in forges.values():
            assert isinstance(forge, Mapping)
            ci = forge["ci"]
            assert isinstance(ci, Mapping)
            assert set(ci) == {"id", "revision_oid", "status"}

    def test_publication_verifier_rejects_unverified_and_malformed_results(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        identity = {
            "tag_object_oid": "a" * 40,
            "commit_oid": "b" * 40,
            "tree_oid": "c" * 40,
        }
        arguments = VERIFY_ARGUMENTS
        hosted = {
            "repository": "example/repository",
            "ci": {"id": 1, "revision_oid": "b" * 40, "status": "success"},
            "release": {},
            "assets": {},
        }
        for result, message in (
            ({"verified": False}, "did not verify"),
            ({"verified": True, "forges": []}, "malformed"),
        ):
            mocker.patch.object(publication.git, "collect", return_value=identity)
            mocker.patch.object(publication.gitlab, "collect", return_value=hosted)
            mocker.patch.object(publication.github, "collect", return_value=hosted)
            mocker.patch.object(
                publication.evaluator,
                "evaluate",
                side_effect=lambda *_args, result=result: result,
            )
            with (
                subtests.test(result=result),
                pytest.raises(publication.PublicationError, match=message),
            ):
                publication.verify(**arguments)

    def test_publication_evidence_is_deeply_frozen(self, *, mocker: MockerFixture) -> None:
        evidence = verified_evidence(forge_evidence(items=[{"value": 1}]), mocker=mocker)
        forges = evidence["forges"]
        assert isinstance(forges, Mapping)
        gitlab = forges["gitlab"]
        assert isinstance(gitlab, Mapping)
        items = gitlab["items"]
        assert isinstance(items, tuple)
        item = items[0]
        assert isinstance(item, Mapping)
        with pytest.raises(TypeError):
            operator.setitem(item, "value", 2)
