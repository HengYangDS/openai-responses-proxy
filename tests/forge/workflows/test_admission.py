"""Event, trust-role and branch admission across the peer projections."""

import re
import subprocess

import pytest

from tests.forge.workflows.fixtures import _load_yaml
from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _string
from tests.forge.workflows.fixtures import _strings
from tests.quality.fixtures import ROOT

GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


@pytest.mark.repository_toolchain
@pytest.mark.parametrize(
    ("result", "allowed"),
    [("success", True), ("failure", False), ("cancelled", False), ("skipped", False)],
)
def test_branch_admission_rejects_any_incomplete_reusable_verification(
    result: str, allowed: bool
) -> None:
    """Keep one required branch check absent on release and fail closed on skipped proof."""
    path = ROOT / ".github/workflows/admission.yml"
    assert path.is_file(), "branch admission workflow is missing"
    branch = _load_yaml(path)
    assert branch["on"] == {
        "pull_request": {"branches": ["dev", "main"]},
        "push": {"branches": ["dev", "main"]},
    }
    jobs = _mapping(branch["jobs"])
    assert set(jobs) == {"verify", "admission"}
    caller = _mapping(jobs["verify"])
    assert caller["uses"] == "./.github/workflows/verify.yml"
    trust = "CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST"
    assert _mapping(caller["secrets"]) == {
        trust: "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
    }
    admission = _mapping(jobs["admission"])
    assert admission["name"] == "Admission"
    assert admission["needs"] == "verify"
    assert admission["if"] == "always()"
    steps = _sequence(admission["steps"])
    assert len(steps) == 1
    step = _mapping(steps[0])
    assert _mapping(step["env"])["VERIFY_RESULT"] == "${{ needs.verify.result }}"
    completed = subprocess.run(
        ("bash", "-e", "-c", _string(step["run"])),
        check=False,
        env={"VERIFY_RESULT": result},
    )
    assert (completed.returncode == 0) is allowed

    verify = _load_yaml(ROOT / ".github/workflows/verify.yml")
    verify_triggers = _mapping(verify["on"])
    assert "workflow_call" in verify_triggers
    assert "pull_request" not in verify_triggers
    assert verify_triggers["push"] == {"tags": ["v*"]}
    assert _mapping(_mapping(verify_triggers["workflow_call"])["secrets"]) == {
        trust: {"required": "true"},
        "CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY": {"required": "false"},
    }


def test_forge_workflows_partition_review_accepted_and_release_proof() -> None:
    """Project the same lifecycle contexts without duplicate proposal pipelines."""
    github = _load_yaml(ROOT / ".github/workflows/verify.yml")
    github_triggers = github["on"]
    assert github_triggers == {
        "workflow_call": {
            "secrets": {
                "CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST": {"required": "true"},
                "CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY": {"required": "false"},
            }
        },
        "push": {"tags": ["v*"]},
        "release": {"types": ["published"]},
        "workflow_dispatch": {
            "inputs": {
                "release_tag": {
                    "description": "Published vMAJOR.MINOR.PATCH tag to verify",
                    "required": "true",
                    "type": "string",
                }
            }
        },
    }
    github_jobs = _mapping(github["jobs"])
    product_proof = (
        "(github.event_name == 'pull_request' && github.base_ref == 'dev') || "
        "(github.event_name == 'push' && github.ref == 'refs/heads/dev')"
    )
    for job_id in (
        "source-and-governance",
        "python",
        "python-windows",
        "python-quality",
        "performance",
    ):
        assert _mapping(github_jobs[job_id])["if"] == product_proof
    native_proof = product_proof + " || (github.event_name == 'push' && github.ref_type == 'tag')"
    for job_id in ("native-assets", "native-linux", "native-linux-lifecycle"):
        assert _mapping(github_jobs[job_id])["if"] == native_proof
    compatibility = _mapping(github_jobs["release-compatibility"])
    assert compatibility["if"] == product_proof
    product_sha = "${{ github.event.pull_request.head.sha || github.sha }}"
    for job_id in (
        "python-matrix",
        "source-and-governance",
        "python",
        "python-windows",
        "python-quality",
        "performance",
        "native-assets",
        "native-linux",
        "native-linux-lifecycle",
        "release-compatibility",
    ):
        checkout = next(
            _mapping(step)
            for step in _sequence(_mapping(github_jobs[job_id])["steps"])
            if str(_mapping(step).get("uses", "")).startswith("actions/checkout@")
        )
        assert _mapping(checkout["with"])["ref"] == product_sha
    assert _mapping(github_jobs["accepted-source"])["if"] == (
        "github.event_name == 'push' && github.ref_type == 'branch'"
    )
    assert _mapping(github_jobs["promotion"])["if"] == (
        "github.event_name == 'pull_request' && github.base_ref == 'main' && "
        "github.head_ref == 'dev'"
    )
    assert _mapping(github_jobs["tag-metadata"])["if"] == (
        "github.event_name == 'push' && github.ref_type == 'tag'"
    )
    tag_steps = _sequence(_mapping(github_jobs["tag-metadata"])["steps"])
    assert any(
        re.fullmatch(r"jdx/mise-action@[0-9a-f]{40}", str(_mapping(step).get("uses", "")))
        for step in tag_steps
    )

    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    rules = _sequence(_mapping(gitlab["workflow"])["rules"])
    assert any(
        _mapping(rule).get("if") == '$CI_PIPELINE_SOURCE == "merge_request_event"' for rule in rules
    )
    assert {"if": '$CI_COMMIT_BRANCH == "dev" || $CI_COMMIT_BRANCH == "main"'} in rules
    assert {
        "if": "$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS",
        "when": "never",
    } in rules
    for job_id in ("verify-python", "verify-python-quality", "verify-performance"):
        job = _mapping(gitlab[job_id])
        assert job["rules"] == [
            {
                "if": '$CI_PIPELINE_SOURCE == "merge_request_event" && '
                '$CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "dev"'
            },
        ]
        accepted = _mapping(gitlab[job_id + "-accepted"])
        assert accepted["rules"] == [{"if": '$CI_COMMIT_BRANCH == "dev"'}]
    python = _mapping(gitlab["verify-python"])
    matrix_values = _sequence(_mapping(python["parallel"])["matrix"])
    matrix = _mapping(matrix_values[0])
    assert matrix["PYTHON_VERSION"] == ["3.12", "3.13", "3.14"]
    python_script = _strings(python["script"])
    assert "nox -s full" not in "\n".join(python_script)
    assert any('nox -s "tests-$PYTHON_VERSION"' in command for command in python_script)
    assert "verify-accepted-source" in gitlab
    promotion = _mapping(gitlab["verify-promotion"])
    assert promotion["rules"] == [
        {
            "if": '$CI_PIPELINE_SOURCE == "merge_request_event" && '
            '$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME == "dev" && '
            '$CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "main"'
        }
    ]
    tag = _mapping(gitlab["verify-release-tag"])
    assert tag["rules"] == [{"if": "$CI_COMMIT_TAG"}]
    assert _mapping(tag["variables"])["GIT_DEPTH"] == "0"
    tag_before_script = _strings(tag["before_script"])
    tag_script = _strings(tag["script"])
    assert "git fetch --tags --force --prune --prune-tags origin" in tag_before_script
    assert 'test -f "${OPENAI_RESPONSES_PROXY_GITLAB_TAG_TRUST:-}"' in tag_before_script
    assert any("tools.release.metadata --tag" in command for command in tag_script)
    assert any("tools.release.tag_signature" in command for command in tag_script)


@pytest.mark.parametrize("review", [True, False])
def test_gitlab_workflow_selects_the_runner_for_its_trust_role(review: bool) -> None:
    """Schedule review jobs independently of the protected branch runner choice."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    protected = "OPENAI_RESPONSES_PROXY_GITLAB_LINUX_RUNNER_TAG"
    unprotected = "OPENAI_RESPONSES_PROXY_GITLAB_LINUX_REVIEW_RUNNER_TAG"
    project_choices = {protected: "protected-linux", unprotected: "review-linux"}
    variables = {**_mapping(gitlab["variables"]), **project_choices}
    suffix = "" if review else "-accepted"
    for name in (
        "source-and-governance",
        "verify-python",
        "verify-python-quality",
        "verify-performance",
    ):
        job = _mapping(gitlab[name + suffix])
        tags = _strings(job.get("tags", _mapping(gitlab["default"])["tags"]))
        expanded = [
            re.sub(r"\$([A-Z_]+)", lambda match: _string(variables[match[1]]), tag) for tag in tags
        ]
        assert expanded == ["review-linux" if review else "protected-linux"]
        expected_rule = (
            '$CI_PIPELINE_SOURCE == "merge_request_event" && '
            '$CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "dev"'
            if review
            else '$CI_COMMIT_BRANCH == "dev"'
        )
        assert job["rules"] == [{"if": expected_rule}]


def test_commit_event_inputs_reach_each_governance_context() -> None:
    github = _load_yaml(ROOT / ".github/workflows/verify.yml")
    jobs = _mapping(github["jobs"])
    job = _mapping(jobs["commit-admission"])
    assert job["if"] == ("github.event_name == 'pull_request' || github.event_name == 'push'")
    steps = [_mapping(step) for step in _sequence(job["steps"])]
    (admission,) = [step for step in steps if "hook commit-range" in str(step.get("run", ""))]
    command = _string(admission["run"])
    assert "--target-ref" in command
    assert "--trusted-baseline" in command
    assert "--proposed-head" in command
    assert "--remote-head" in command
    assert '--remote "$OPENAI_RESPONSES_PROXY_COMMIT_PEER"' in command
    environment = _mapping(admission["env"])
    assert environment["OPENAI_RESPONSES_PROXY_COMMIT_HEAD"] == (
        "${{ github.event.pull_request.head.sha || github.sha }}"
    )
    assert environment["OPENAI_RESPONSES_PROXY_COMMIT_BASE"] == (
        "${{ github.ref_type == 'tag' && github.sha || github.event.pull_request.base.sha || github.event.before }}"
    )
    assert environment["OPENAI_RESPONSES_PROXY_COMMIT_TARGET"] == (
        "${{ github.event_name == 'pull_request' && format('refs/heads/{0}', github.base_ref) || github.ref }}"
    )
    assert environment["OPENAI_RESPONSES_PROXY_COMMIT_PEER"] == "github"
    branch = _mapping(jobs["branch-proof"])
    assert "commit-admission" in _sequence(branch["needs"])

    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    job = _mapping(gitlab["commit-admission"])
    (command,) = _strings(job["script"])
    assert "hook commit-range" in command
    assert _mapping(job["variables"])["OPENAI_RESPONSES_PROXY_COMMIT_HEAD"] == "$CI_COMMIT_SHA"
    review_rule = _mapping(_sequence(job["rules"])[0])
    assert _mapping(review_rule["variables"]) == {
        "OPENAI_RESPONSES_PROXY_COMMIT_BASE": "$CI_MERGE_REQUEST_DIFF_BASE_SHA",
        "OPENAI_RESPONSES_PROXY_COMMIT_TARGET": "refs/heads/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
    }
    assert '--remote "$OPENAI_RESPONSES_PROXY_COMMIT_PEER"' in command


@pytest.mark.parametrize("event", ["review", "push", "api", "tag"])
def test_gitlab_commit_projection_declares_native_event_values(event: str) -> None:
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    head, base, review = "a" * 40, "b" * 40, "c" * 40
    zero = "0" * 40
    inputs = {
        "CI_COMMIT_SHA": head,
        "CI_COMMIT_BEFORE_SHA": zero if event == "api" else base,
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": review if event == "review" else "",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main" if event == "review" else "",
        "CI_COMMIT_REF_NAME": "v4.0.3" if event == "tag" else "dev",
        "CI_COMMIT_TAG": "v4.0.3" if event == "tag" else "",
    }
    job_id = "commit-admission" if event == "review" else "commit-admission-accepted"
    job = _mapping(gitlab[job_id])
    rules = tuple(_mapping(rule) for rule in _sequence(job["rules"]))
    rule = rules[0 if event in {"review", "tag"} else 1]
    assert rule["if"] == {
        "review": '$CI_PIPELINE_SOURCE == "merge_request_event"',
        "tag": "$CI_COMMIT_TAG",
    }.get(event, '$CI_COMMIT_BRANCH == "dev" || $CI_COMMIT_BRANCH == "main"')
    declared = {**_mapping(job["variables"]), **_mapping(rule["variables"])}
    projected = {
        key: re.sub(r"\$([A-Z_]+)", lambda match: inputs[match[1]], _string(value))
        for key, value in declared.items()
        if key.startswith("OPENAI_RESPONSES_PROXY_COMMIT_")
        and key != "OPENAI_RESPONSES_PROXY_COMMIT_ROOT"
    }
    assert projected == {
        "OPENAI_RESPONSES_PROXY_COMMIT_BASE": {
            "review": review,
            "push": base,
            "api": zero,
            "tag": head,
        }[event],
        "OPENAI_RESPONSES_PROXY_COMMIT_HEAD": head,
        "OPENAI_RESPONSES_PROXY_COMMIT_TARGET": {
            "review": "refs/heads/main",
            "tag": "refs/tags/v4.0.3",
        }.get(event, "refs/heads/dev"),
        "OPENAI_RESPONSES_PROXY_COMMIT_PEER": "gitlab",
    }
