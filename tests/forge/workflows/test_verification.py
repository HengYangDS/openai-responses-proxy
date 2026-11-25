"""Shared verification graph, Python compatibility and syntax contracts."""

import re

from openai_responses_proxy import product_identity
from tests.forge.workflows.fixtures import _load_yaml
from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _string
from tests.forge.workflows.fixtures import _strings
from tests.quality.fixtures import ROOT

GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


def _assert_github_required_tokens(text: str) -> None:
    required = [
        "name: Verify",
        "push:",
        "permissions:\n  contents: read",
        'GIT_CONFIG_COUNT: "1"',
        "GIT_CONFIG_KEY_0: init.defaultBranch",
        "GIT_CONFIG_VALUE_0: main",
        "actions/checkout@",
        "uv run --locked --no-sync python -m tools.quality.python_matrix",
        "python-version: ${{ fromJSON(needs.python-matrix.outputs.versions) }}",
        'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"',
        "actions/setup-python@",
        "fetch-tags: true",
        "if: github.event_name == 'push' && github.ref_type == 'tag'",
        "python -m tools.quality.governance --online-links",
        'uv run --locked --no-sync python -m tools.release.metadata --tag "$GITHUB_REF_NAME"',
        "uv run --locked --no-sync python -m tools.release.metadata",
        "python-quality:",
        "astral-sh/setup-uv@",
        "uv run --locked --group quality nox -s quality",
        (
            "python -m pytest -q --rootdir . -c .config/checks/pytest/pytest.toml "
            "tests/quality/test_policy.py tests/quality/repository "
            "tests/quality/orchestration/test_governance.py tests/forge/workflows"
        ),
        "tests/release/publication",
        "native-assets:",
        "name: Native asset (${{ matrix.platform }})",
        "native-linux:",
        "container: ${{ needs.python-matrix.outputs.linux-release-image }}",
        'uv run --locked --no-sync nox -s release -- "${{ runner.temp }}/native-assets"',
        "actions/upload-artifact@",
        "release-assets:",
        "name: Release assets",
        "python-version: ${{ needs.python-matrix.outputs.latest }}",
        "name: Download native release assets",
        "GH_TOKEN: ${{ github.token }}",
        """gh run download "$GITHUB_RUN_ID" --pattern 'native-*' """
        """--dir "$RUNNER_TEMP/native" """.rstrip(),
        "uv run --locked --no-sync python -m tools.release.artifact assemble",
        "CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY",
        "OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST",
        'install -m 600 /dev/null "$RUNNER_TEMP/release-asset-signing-key"',
        (
            "printf '%s\\n' \"$RELEASE_ASSET_SIGNING_KEY_TEXT\" "
            '> "$RUNNER_TEMP/release-asset-signing-key"'
        ),
        "RELEASE_ASSET_SIGNING_KEY_PATH: ${{ runner.temp }}/release-asset-signing-key",
        "--sign",
        "name: release-assets",
    ]
    for token in required:
        if token not in text:
            raise AssertionError(f"GitHub Actions verification contract is missing {token!r}")
    if "contents: write" in text:
        raise AssertionError("verification workflow must use read-only repository permissions")


def _assert_github_matrix_contract(text: str) -> None:
    matrix_start = text.index("\n  python-matrix:")
    matrix_end = text.index("\n  python:", matrix_start)
    matrix_block = text[matrix_start:matrix_end]
    for token in (
        "astral-sh/setup-uv@",
        "uv sync --locked --all-groups",
        "uv run --locked --no-sync python -m tools.quality.python_matrix",
    ):
        if token not in matrix_block:
            raise AssertionError(
                f"Python matrix bootstrap must install and use the locked environment: {token!r}"
            )
    if "needs.native-assets.outputs.python-version" in text:
        raise AssertionError(
            "release assembly must read the Python SSOT, not a pass-through job output"
        )
    if (
        "RELEASE_ASSET_SIGNING_KEY_PATH: "
        "${{ secrets.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY }}"
    ) in text:
        raise AssertionError("the product signer must receive a key path, not secret text")
    if "pull_request_target:" in text:
        raise AssertionError("verification workflow must not execute privileged pull-request code")
    if "@main" in text or "@master" in text:
        raise AssertionError("GitHub Actions must use immutable action revisions")


def _assert_github_platform_contract(text: str) -> None:
    mac_start = text.index("\n  python:")
    governance_start = text.index("\n  accepted-source:")
    mac_block = text[mac_start:governance_start]
    setup_uv = "astral-sh/setup-uv@"
    if mac_block.count(setup_uv) != 1:
        raise AssertionError("macOS Python matrix must use exactly one setup-uv action")
    if mac_block.count("actions/setup-python@") != 1:
        raise AssertionError("macOS Python matrix must use exactly one pinned setup-python action")
    test_owner = 'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"'
    if test_owner not in mac_block:
        raise AssertionError(f"macOS Python matrix must run {test_owner}")
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    host_native_jobs = (
        "source-and-governance",
        "python",
        "accepted-source",
        "promotion",
        "tag-metadata",
        "python-quality",
        "native-assets",
        "release-assets",
        "published-release-compatibility",
    )
    for job_id in host_native_jobs:
        steps = _sequence(_mapping(jobs[job_id])["steps"])
        assert any(
            _string(_mapping(step).get("uses", "")).startswith("actions/setup-python@")
            for step in steps
        ), f"{job_id} must use pinned setup-python"
    quality_start = text.index("\n  python-quality:")
    quality_end = text.index("\n  native-assets:", quality_start)
    quality_block = text[quality_start:quality_end]
    for token in ("fetch-depth: 0", "fetch-tags: true"):
        if token not in quality_block:
            raise AssertionError(f"quality checkout must contain {token!r}")
    for patch_pin in ("3.12.", "3.13.", "3.14."):
        if patch_pin in text:
            raise AssertionError(
                "GitHub workflows must select supported Python lines, not patch releases: "
                f"{patch_pin!r}"
            )


def _assert_github_governance_contract(text: str) -> None:
    accepted_start = text.index("\n  accepted-source:")
    tag_start = text.index("\n  tag-metadata:")
    quality_start = text.index("\n  python-quality:", tag_start)
    accepted_block = text[accepted_start:tag_start]
    tag_block = text[tag_start:quality_start]
    for block in (accepted_block, tag_block):
        for token in ("fetch-depth: 0", "fetch-tags: true"):
            if token not in block:
                raise AssertionError(f"governance checkout must contain {token!r}")
    tag_check = (
        'uv run --locked --no-sync python -m tools.release.metadata --tag "$GITHUB_REF_NAME"'
    )
    branch_check = "uv run --locked --no-sync python -m tools.release.metadata"
    if tag_check not in tag_block:
        raise AssertionError("tag metadata must validate the exact annotated tag")
    if branch_check not in accepted_block:
        raise AssertionError("accepted source must validate mainline metadata")
    if "--prepare-release" in accepted_block:
        raise AssertionError("accepted source verification must not require release preparation")


def _assert_github_native_and_forbidden_contract(text: str) -> None:
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    for job_id in ("native-assets", "native-linux"):
        for step in _sequence(_mapping(jobs[job_id])["steps"]):
            native_step = _mapping(step)
            assert "shell" not in native_step
            assert "set -euo pipefail" not in _string(native_step.get("run", ""))
    if re.search(r"(?:^|\s)\S+\.sh(?:\s|$)", text):
        raise AssertionError("GitHub verification must not depend on repository shell scripts")
    for forbidden in (
        "self-hosted",
        "openai-responses-proxy-github-macos-arm64",
        "/opt/homebrew",
        "refs/openai-responses-proxy/runner-checkout-retained",
        "git update-ref",
    ):
        if forbidden in text:
            raise AssertionError(
                f"GitHub workflows must not depend on runner-local state: {forbidden!r}"
            )
    for forbidden in ("git config --global", "GIT_ADVICE", "advice.detachedHead"):
        if forbidden in text:
            raise AssertionError(
                f"GitHub workflows must not suppress Git diagnostics with {forbidden!r}"
            )


def test_github_verification_workflow_contract() -> None:
    text = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    _assert_github_required_tokens(text)
    _assert_github_matrix_contract(text)
    _assert_github_platform_contract(text)
    _assert_github_governance_contract(text)
    _assert_github_native_and_forbidden_contract(text)
    source = text.split("\n  source-and-governance:", 1)[1].split("\n  python:", 1)[0]
    assert source.count("python -m tools.quality.governance --online-links") == 1
    for duplicate in (
        "cue vet",
        "openspec validate --all",
        "actionlint .github/workflows",
        "gitleaks git",
        "python -m tools.release.metadata",
        "python -m tools.quality.repository",
    ):
        assert duplicate not in source


def test_gitlab_pytest_invocations_preserve_repository_module_resolution() -> None:
    text = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")

    if f"{GITLAB_LOCKED_PYTHON} pytest" in text:
        raise AssertionError("GitLab must not invoke the pytest console script directly")
    assert f"{GITLAB_LOCKED_PYTHON} pytest" not in text


def _legs(job: object) -> dict[str, str]:
    """Return a platform matrix job's legs as platform to runner."""
    legs = _sequence(_mapping(_mapping(_mapping(job)["strategy"])["matrix"])["include"])
    return {_string(_mapping(leg)["platform"]): _string(_mapping(leg)["runner"]) for leg in legs}


def test_every_released_platform_has_one_runner_in_every_github_job() -> None:
    """Platforms come from the product inventory and runners from one declaration."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    runners = _legs(jobs["release-compatibility"])
    assert set(runners) == set(product_identity.RELEASE_PLATFORMS)
    assert _legs(jobs["published-release-compatibility"]) == runners
    native_names = {
        _string(_mapping(jobs["native-assets"])["name"]).replace("${{ matrix.platform }}", leg)
        for leg in _legs(jobs["native-assets"])
    } | {_string(_mapping(jobs["native-linux"])["name"])}
    assert native_names == {
        f"Native asset ({platform})" for platform in product_identity.RELEASE_PLATFORMS
    }
    for platform, runner in _legs(jobs["native-assets"]).items():
        assert runner == runners[platform]
    fixed = {
        job_id: _string(_mapping(job)["runs-on"])
        for job_id, job in jobs.items()
        if "${{" not in _string(_mapping(job)["runs-on"])
    }
    assert set(fixed.values()) <= set(runners.values()), fixed
    assert fixed["python"] in set(runners.values()) - {fixed["native-linux"]}


def test_gitlab_runs_linux_jobs_alone_and_github_runs_python_on_its_macos_builder() -> None:
    """GitLab's macOS job waits for a later Change (D2); GitHub's reads the declared runner."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    default = _mapping(gitlab["default"])
    linux_roles = {
        tuple(_strings(default["tags"])),
        tuple(_strings(_mapping(gitlab["commit-admission"])["tags"])),
    }
    jobs = {
        name: _mapping(job)
        for name, job in gitlab.items()
        if isinstance(job, dict) and "stage" in job
    }
    assert jobs
    for name, job in jobs.items():
        assert "inherit" not in job, name
        assert "image" in job or "image" in default, name
        assert tuple(_strings(job.get("tags", default["tags"]))) in linux_roles, name

    github = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    (macos_runner,) = _legs(github["native-assets"]).values()
    assert _mapping(github["python"])["runs-on"] == macos_runner
