"""One Forge-neutral dependency proposal policy reads every pinned input once."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping

from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _strings
from tests.quality.fixtures import ROOT

POLICY = ROOT / ".config/checks/renovate/renovate.json5"
# Renovate and Dependabot discover repository configuration here; a file at any of these paths
# would let a Forge open proposals that the one policy does not own.
DISCOVERED = (
    "renovate.json",
    "renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
    ".renovaterc.json5",
    ".github/renovate.json",
    ".github/renovate.json5",
    ".gitlab/renovate.json",
    ".gitlab/renovate.json5",
    ".github/dependabot.yml",
    ".github/dependabot.yaml",
)
# Native managers read the locks; every digest pin elsewhere in the inputs belongs to one rule.
LOCKS = frozenset({"uv.lock", "package-lock.json", ".config/mise/mise.lock"})
DIGEST_PIN = re.compile(r"@(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})")


def _policy() -> Mapping[str, object]:
    return _mapping(json.loads(POLICY.read_text(encoding="utf-8")))


def _rules(policy: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    return [_mapping(rule) for rule in _sequence(policy[key])]


def _python_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one Renovate pattern, whose named groups use JavaScript syntax."""
    return re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", pattern))


def _extracted(policy: Mapping[str, object]) -> list[tuple[str, dict[str, str]]]:
    """Every input path and capture that the custom rules extract."""
    inputs = _strings(policy["includePaths"])
    found: list[tuple[str, dict[str, str]]] = []
    for rule in _rules(policy, "customManagers"):
        patterns = [pattern.strip("/") for pattern in _strings(rule["managerFilePatterns"])]
        files = [path for path in inputs if any(re.search(p, path) for p in patterns)]
        assert files, rule["description"]
        for path in files:
            text = (ROOT / path).read_text(encoding="utf-8")
            for pattern in _strings(rule["matchStrings"]):
                for match in _python_pattern(pattern).finditer(text):
                    groups = {k: v for k, v in match.groupdict().items() if v is not None}
                    found.append((path, {**groups, "rule": str(rule["description"])}))
    return found


def test_no_forge_discovers_a_second_proposal_owner() -> None:
    assert [name for name in DISCOVERED if (ROOT / name).exists()] == []
    assert "renovate" not in json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    for projection in (ROOT / ".gitlab-ci.yml", *workflows):
        text = projection.read_text(encoding="utf-8").lower()
        assert "renovate" not in text, projection.name
        assert "dependabot" not in text, projection.name


def test_proposals_serialize_on_one_branch_line() -> None:
    policy = _policy()
    assert policy["baseBranchPatterns"] == ["dev"]
    assert policy["branchPrefix"] == "proposal/dependencies/"
    assert policy["prConcurrentLimit"] == policy["branchConcurrentLimit"] == 1
    # The maintenance run selects one Forge; the repository policy names none.
    assert not {"platform", "endpoint", "repositories", "baseBranches"} & policy.keys()


def test_proposal_commits_follow_the_commit_policy() -> None:
    policy = _policy()
    workspace = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))
    assert policy["semanticCommits"] == "enabled"
    assert policy["semanticCommitType"] == "build"
    assert policy["semanticCommitScope"] in workspace["commit_policy"]["scopes"]


def test_policy_reads_authored_inputs_not_forge_projections() -> None:
    policy = _policy()
    assert policy["enabledManagers"] == ["pep621", "npm", "mise", "custom.regex"]
    inputs = _strings(policy["includePaths"])
    assert [path for path in inputs if not (ROOT / path).is_file()] == []
    projections = [path for path in inputs if path.startswith(".github/")]
    assert projections + [path for path in inputs if path == ".gitlab-ci.yml"] == []


def test_every_digest_pin_is_read_by_exactly_one_rule() -> None:
    policy = _policy()
    found = _extracted(policy)
    pins = 0
    for path in _strings(policy["includePaths"]):
        if path in LOCKS:
            continue
        for pin in DIGEST_PIN.finditer((ROOT / path).read_text(encoding="utf-8")):
            pins += 1
            digest = pin.group(0).removeprefix("@")
            readers = [
                groups
                for found_path, groups in found
                if found_path == path and groups.get("currentDigest") == digest
            ]
            assert len(readers) == 1, (path, digest, readers)
            assert readers[0].get("depName", "") != "", readers[0]
            assert readers[0].get("currentValue", "") != "", readers[0]
    assert pins == sum(1 for _, groups in found if "currentDigest" in groups)


def test_uv_required_version_is_read_with_the_uv_group() -> None:
    policy = _policy()
    uv_config = tomllib.loads((ROOT / ".config/mise/uv.toml").read_text(encoding="utf-8"))
    required = str(uv_config["required-version"]).removeprefix("==")
    readers = [groups for path, groups in _extracted(policy) if path == ".config/mise/uv.toml"]
    assert [groups["currentValue"] for groups in readers] == [required]
    custom = _rules(policy, "customManagers")
    (uv_rule,) = [rule for rule in custom if rule.get("depNameTemplate") == "uv"]
    assert uv_rule["packageNameTemplate"] == "astral-sh/uv"
    groups = [rule for rule in _rules(policy, "packageRules") if rule.get("groupName") == "uv"]
    assert [_strings(rule["matchDepNames"]) for rule in groups] == [["uv", "ghcr.io/astral-sh/uv"]]


def test_release_age_vulnerability_and_automerge_admission() -> None:
    policy = _policy()
    rules = _rules(policy, "packageRules")
    assert policy["minimumReleaseAge"] == "3 days"
    assert policy["minimumReleaseAgeBehaviour"] == "timestamp-required"
    managers = [(_strings(rule.get("matchManagers", [])), rule) for rule in rules]
    assert [
        rule for names, rule in managers if "npm" in names and "minimumReleaseAge" in rule
    ] == []
    vulnerability = _mapping(policy["vulnerabilityAlerts"])
    assert vulnerability["minimumReleaseAge"] == "0 days"
    # Without their own concurrency limits, security fixes skip the proposal queue, as
    # Renovate does by default.
    assert not {"prConcurrentLimit", "branchConcurrentLimit"} & vulnerability.keys()
    assert vulnerability["automerge"] is False
    assert policy["automerge"] is False
    assert policy["platformAutomerge"] is False
    automerged = [rule for rule in rules if rule.get("automerge") is True]
    assert automerged
    for rule in automerged:
        assert set(_strings(rule["matchManagers"])) <= {"pep621", "npm"}
        assert set(_strings(rule["matchUpdateTypes"])) <= {"minor", "patch"}
        assert rule["matchCurrentVersion"] == ">=1.0.0"
    python = [rule for rule in rules if rule.get("matchDepNames") == ["python"]]
    disabled = [rule["matchManagers"] for rule in python if rule.get("enabled") is False]
    assert disabled == [["pep621"]]
    assert [rule["allowedVersions"] for rule in python if "allowedVersions" in rule] == ["<3.15"]
