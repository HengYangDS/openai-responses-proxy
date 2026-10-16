"""Behavioral acceptance of the generated branch-proof command."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.ci.admission import admit
from tools.ci.admission import main

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_PROOF_JOBS = (
    "source-and-governance",
    "python-matrix",
    "python",
    "python-windows",
    "python-quality",
    "performance",
    "native-assets",
    "native-linux",
    "native-linux-lifecycle",
    "release-compatibility",
)


@pytest.fixture
def proof() -> dict[str, object]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    value = workflow["jobs"]["branch-proof"]
    assert isinstance(value, dict)
    return {str(key): item for key, item in value.items()}


@pytest.fixture
def environment(proof: dict[str, object]) -> dict[str, str]:
    steps = proof["steps"]
    assert isinstance(steps, list)
    declared = steps[-1]["env"]
    assert isinstance(declared, dict)
    return {
        **{str(key): str(value) for key, value in declared.items()},
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/tags/v5.0.6",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_BASE_REF": "",
        "NEEDS_JSON": "{}",
    }


def _run(command: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    arguments = shlex.split(command)
    assert arguments[0] == "python"
    return subprocess.run(
        (sys.executable, *arguments[1:]),
        cwd=ROOT,
        env={**os.environ, **environment},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize(
    "needs",
    [None, [], {"proof": []}, {"proof": {"result": "unknown"}}],
)
def test_branch_admission_refuses_malformed_results_without_a_traceback(
    needs: object, environment: dict[str, str]
) -> None:
    environment["NEEDS_JSON"] = json.dumps(needs)
    result = _run("python -m tools.ci.admission", environment)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_branch_admission_is_one_native_command_with_declarative_policy(
    proof: dict[str, object], environment: dict[str, str]
) -> None:
    steps = proof["steps"]
    assert isinstance(steps, list)
    step = steps[-1]
    assert step["run"] == "python -m tools.ci.admission"
    assert isinstance(json.loads(step["env"]["BRANCH_ADMISSION_RULES"]), list)
    assert steps[0]["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert proof["if"] == "always()"
    required = proof["needs"]
    assert isinstance(required, list)
    assert set(required) == {*PRODUCT_PROOF_JOBS, "accepted-source", "promotion"}
    assert _run(step["run"], environment).returncode == 0


@pytest.mark.parametrize(
    ("event", "ref", "base", "required"),
    [
        ("pull_request", "refs/pull/1/merge", "dev", PRODUCT_PROOF_JOBS),
        ("push", "refs/heads/dev", "", (*PRODUCT_PROOF_JOBS, "accepted-source")),
        ("pull_request", "refs/pull/2/merge", "main", ("promotion",)),
        ("push", "refs/heads/main", "", ("accepted-source",)),
        ("push", "refs/tags/v5.0.6", "", ()),
        ("release", "refs/tags/v5.0.6", "", ()),
        ("workflow_dispatch", "refs/heads/main", "", ()),
    ],
)
def test_each_event_requires_complete_successful_proof(
    event: str, ref: str, base: str, required: tuple[str, ...], environment: dict[str, str]
) -> None:
    environment.update(
        GITHUB_EVENT_NAME=event,
        GITHUB_REF=ref,
        GITHUB_BASE_REF=base,
        GITHUB_REF_TYPE="tag" if ref.startswith("refs/tags/") else "branch",
    )
    needs = {job: {"result": "success"} for job in required}
    environment["NEEDS_JSON"] = json.dumps(needs)
    assert admit(environment) == required
    for job in required:
        for result in ("skipped", "failure", "cancelled"):
            needs[job]["result"] = result
            environment["NEEDS_JSON"] = json.dumps(needs)
            with pytest.raises(ValueError, match="required branch proof failed"):
                admit(environment)
        needs[job]["result"] = "success"
    environment["NEEDS_JSON"] = "{}"
    if required:
        with pytest.raises(ValueError, match="required branch proof failed"):
            admit(environment)


@pytest.mark.parametrize("event", ["workflow_call", "push"])
def test_unknown_invocation_context_cannot_pass(event: str, environment: dict[str, str]) -> None:
    environment.update(GITHUB_EVENT_NAME=event, GITHUB_REF_TYPE="other")
    with pytest.raises(ValueError, match="unsupported or ambiguous"):
        admit(environment)


@pytest.mark.parametrize(
    "rules",
    [
        None,
        [],
        [{}],
        [{"when": {}, "jobs": []}],
        [{"when": {"GITHUB_EVENT_NAME": 1}, "jobs": []}],
        [{"when": {"GITHUB_EVENT_NAME": "push"}, "jobs": "proof"}],
        [{"when": {"GITHUB_EVENT_NAME": "push"}, "jobs": [""]}],
        [{"when": {"GITHUB_EVENT_NAME": "push"}, "jobs": ["proof", "proof"]}],
    ],
)
def test_invalid_policy_has_no_admission_authority(
    rules: object, environment: dict[str, str]
) -> None:
    environment["BRANCH_ADMISSION_RULES"] = json.dumps(rules)
    with pytest.raises(ValueError, match="branch admission"):
        admit(environment)


def test_overlapping_event_rules_are_not_an_implicit_priority(environment: dict[str, str]) -> None:
    rules = json.loads(environment["BRANCH_ADMISSION_RULES"])
    rules.append({"when": {"GITHUB_EVENT_NAME": "push"}, "jobs": []})
    environment["BRANCH_ADMISSION_RULES"] = json.dumps(rules)
    with pytest.raises(ValueError, match="ambiguous"):
        admit(environment)


def test_native_cli_refuses_missing_json_without_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRANCH_ADMISSION_RULES", raising=False)
    with pytest.raises(SystemExit, match="JSON input is unavailable"):
        main()
    monkeypatch.setenv("BRANCH_ADMISSION_RULES", "{")
    with pytest.raises(SystemExit, match="JSON input is unavailable"):
        main()
