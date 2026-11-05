"""Pure fail-closed evaluation for independent dual-Forge publications."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final
from typing import cast

from openai_responses_proxy import product_identity
from openai_responses_proxy.serving.digest import is_sha256
from tools.release import identity
from tools.release.artifact import format as product_assets

_OID: Final = re.compile(r"^[0-9a-f]{40,64}$")
_FORGE_FIELDS: Final = frozenset(
    {
        "provider",
        "repository",
        "tag",
        "tag_object_oid",
        "commit_oid",
        "tree_oid",
        "anchor_sha256",
        "signature_verified",
        "assets",
        "ci",
        "release",
    }
)
_CI_FIELDS: Final = frozenset({"id", "revision_oid", "status"})
_RELEASE_FIELDS: Final = frozenset({"id", "tag", "commit_oid", "name", "draft", "prerelease"})


def evaluate(
    tag: str,
    gitlab: Mapping[str, object],
    github: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate complete evidence without minting installation authority."""
    reasons: list[str] = []
    normalized: dict[str, dict[str, object]] = {}
    for provider, evidence in (
        ("gitlab", gitlab),
        ("github", github),
    ):
        forge, forge_reasons = _evaluate_forge(provider, tag, evidence)
        reasons.extend(forge_reasons)
        if forge is not None:
            normalized[provider] = forge

    tag_object_equal = (
        "gitlab" in normalized
        and "github" in normalized
        and normalized["gitlab"]["tag_object_oid"] == normalized["github"]["tag_object_oid"]
    )
    commit_equal = (
        "gitlab" in normalized
        and "github" in normalized
        and normalized["gitlab"]["commit_oid"] == normalized["github"]["commit_oid"]
    )
    tree_equal = (
        "gitlab" in normalized
        and "github" in normalized
        and normalized["gitlab"]["tree_oid"] == normalized["github"]["tree_oid"]
    )
    if "gitlab" in normalized and "github" in normalized and not tag_object_equal:
        reasons.append("tag_object_mismatch")
    if "gitlab" in normalized and "github" in normalized and not commit_equal:
        reasons.append("commit_mismatch")
    if "gitlab" in normalized and "github" in normalized and not tree_equal:
        reasons.append("tree_mismatch")
    assets_equal = _common_payloads_equal(normalized, tag)
    if "gitlab" in normalized and "github" in normalized and not assets_equal:
        reasons.append("asset_mismatch")
    trust_anchor_equal = (
        "gitlab" in normalized
        and "github" in normalized
        and normalized["gitlab"]["anchor_sha256"] == normalized["github"]["anchor_sha256"]
    )
    if "gitlab" in normalized and "github" in normalized and not trust_anchor_equal:
        reasons.append("trust_anchor_mismatch")
    verified = (
        not reasons
        and tag_object_equal
        and commit_equal
        and tree_equal
        and assets_equal
        and trust_anchor_equal
    )
    return {
        "schema_version": 1,
        "tag": tag,
        "verified": verified,
        "tag_object_equal": tag_object_equal,
        "commit_equal": commit_equal,
        "tree_equal": tree_equal,
        "assets_equal": assets_equal,
        "trust_anchor_equal": trust_anchor_equal,
        "reasons": reasons,
        "forges": normalized,
    }


def _common_payloads_equal(normalized: Mapping[str, Mapping[str, object]], tag: str) -> bool:
    """Require one complete, byte-identical release inventory on both Forges."""
    del tag
    if not {"gitlab", "github"} <= set(normalized):
        return False
    return normalized["gitlab"]["assets"] == normalized["github"]["assets"]


def _evaluate_forge(
    provider: str,
    tag: str,
    evidence: Mapping[str, object],
) -> tuple[dict[str, object] | None, list[str]]:
    if set(evidence) != _FORGE_FIELDS:
        return None, [f"{provider}.invalid_evidence"]
    if (
        evidence.get("provider") != provider
        or evidence.get("tag") != tag
        or not identity.is_tag(tag)
    ):
        return None, [f"{provider}.invalid_evidence"]
    if not isinstance(evidence.get("repository"), str) or not evidence["repository"]:
        return None, [f"{provider}.invalid_evidence"]
    if any(
        not isinstance(evidence.get(field), str) or not _OID.fullmatch(cast(str, evidence[field]))
        for field in ("tag_object_oid", "commit_oid", "tree_oid")
    ):
        return None, [f"{provider}.invalid_evidence"]
    if not is_sha256(evidence.get("anchor_sha256")):
        return None, [f"{provider}.invalid_evidence"]

    assets = evidence.get("assets")
    typed_assets = _validated_assets(provider, tag, assets)
    if typed_assets is None:
        return None, [f"{provider}.invalid_evidence"]

    reasons: list[str] = []
    if evidence.get("signature_verified") is not True:
        reasons.append(f"{provider}.signature_unverified")
    ci = evidence.get("ci")
    release = evidence.get("release")
    if not isinstance(ci, Mapping) or not isinstance(release, Mapping):
        return None, [f"{provider}.invalid_evidence"]
    typed_ci = cast(Mapping[str, object], ci)
    typed_release = cast(Mapping[str, object], release)
    if set(typed_ci) != _CI_FIELDS or set(typed_release) != _RELEASE_FIELDS:
        return None, [f"{provider}.invalid_evidence"]
    commit = cast(str, evidence["commit_oid"])
    if typed_ci.get("revision_oid") != commit:
        reasons.append(f"{provider}.ci_revision_mismatch")
    if typed_ci.get("status") != "success":
        reasons.append(f"{provider}.ci_not_successful")
    if typed_release.get("tag") != tag:
        reasons.append(f"{provider}.release_tag_mismatch")
    if typed_release.get("commit_oid") != commit:
        reasons.append(f"{provider}.release_commit_mismatch")
    if typed_release.get("name") != product_identity.release_title(tag):
        reasons.append(f"{provider}.release_name_mismatch")
    if typed_release.get("draft") is not False:
        reasons.append(f"{provider}.release_draft")
    if typed_release.get("prerelease") is not False:
        reasons.append(f"{provider}.release_prerelease")
    normalized = {
        "provider": provider,
        "repository": evidence["repository"],
        "tag": tag,
        "tag_object_oid": evidence["tag_object_oid"],
        "commit_oid": commit,
        "tree_oid": evidence["tree_oid"],
        "anchor_sha256": evidence["anchor_sha256"],
        "signature_verified": evidence["signature_verified"],
        "assets": dict(typed_assets),
        "ci": {
            "id": typed_ci.get("id"),
            "revision_oid": typed_ci.get("revision_oid"),
            "status": typed_ci.get("status"),
        },
        "release": {
            "id": typed_release.get("id"),
            "tag": typed_release.get("tag"),
            "commit_oid": typed_release.get("commit_oid"),
            "name": typed_release.get("name"),
            "draft": typed_release.get("draft"),
            "prerelease": typed_release.get("prerelease"),
        },
    }
    return normalized, reasons


def _validated_assets(provider: str, tag: str, assets: object) -> Mapping[str, object] | None:
    """Return the exact complete release inventory declared by the product SSOT."""
    del provider
    if not isinstance(assets, Mapping):
        return None
    typed_assets = cast(Mapping[str, object], assets)
    expected_assets = product_assets.release_asset_names(
        identity.version_from_tag(tag),
        product_assets.RELEASE_PLATFORMS,
    )
    if set(typed_assets) != expected_assets:
        return None
    if any(not is_sha256(typed_assets.get(name)) for name in expected_assets):
        return None
    return typed_assets
