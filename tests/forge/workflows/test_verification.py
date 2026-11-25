"""Shared verification graph, Python compatibility and syntax contracts."""

import re
from collections.abc import Mapping

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


# GitLab's macOS Python job, by role: review for merge requests, protected for dev.
GITLAB_MACOS_JOBS = {"review": "verify-python-macos", "protected": "verify-python-macos-accepted"}
GITLAB_LINUX_JOBS = {"review": "verify-python", "protected": "verify-python-accepted"}


def _gitlab_jobs() -> dict[str, Mapping[str, object]]:
    """Return GitLab's jobs, the entries that name a stage."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    return {
        name: _mapping(job)
        for name, job in gitlab.items()
        if isinstance(job, dict) and "stage" in job
    }


def test_both_forges_run_the_python_compatibility_job_on_macos() -> None:
    """GitLab renders GitHub's macOS Python job from one declaration and requires it (D1-D4)."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    jobs = _gitlab_jobs()
    github = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    github_python = _mapping(github["python"])
    versions = (ROOT / ".config/ci/python-versions").read_text(encoding="utf-8").split()
    # GitHub's matrix is the python-matrix job's reading of the same tracked projection.
    assert github_python["needs"] == "python-matrix"
    assert "needs.python-matrix.outputs.versions" in _string(
        _mapping(_mapping(github_python["strategy"])["matrix"])["python-version"]
    )
    github_session = _string(_mapping(_sequence(github_python["steps"])[-1]).get("run", ""))
    tags = {}
    for role, name in GITLAB_MACOS_JOBS.items():
        job = jobs[name]
        inherited = _mapping(job["inherit"])
        assert {key: str(value).lower() for key, value in inherited.items()} == {
            "default": "false",
            "variables": "false",
        }
        assert "allow_failure" not in job, "the macOS job is required"
        assert "image" not in job
        target = _string(_mapping(job["variables"])["OPENAI_RESPONSES_PROXY_CI_TARGET"])
        assert target in product_identity.RELEASE_PLATFORMS
        assert target != _mapping(gitlab["variables"])["OPENAI_RESPONSES_PROXY_CI_TARGET"]
        matrix = _mapping(_sequence(_mapping(job["parallel"])["matrix"])[0])
        assert _sequence(matrix["PYTHON_VERSION"]) == versions
        session = re.search(r'nox -s "(\w+)-', github_session)
        assert session is not None
        assert any(
            command.endswith(f'nox -s "{session[1]}-$PYTHON_VERSION"')
            for command in _strings(job["script"])
        )
        assert job["timeout"] == f"{github_python['timeout-minutes']}m"
        assert job["rules"] == jobs[GITLAB_LINUX_JOBS[role]]["rules"]
        tags[role] = set(_strings(job["tags"]))
    review, protected = tags["review"], tags["protected"]
    assert len(review) == len(protected)
    assert len(review ^ protected) == 2, "the roles differ by their trust tag alone"


def test_gitlab_macos_steps_are_the_linux_steps_run_through_the_declared_mise() -> None:
    """One step list: the Linux job runs it after its system bootstrap, macOS through Mise."""
    jobs = _gitlab_jobs()
    image = _string(_mapping(jobs["source-and-governance"]["image"])["name"])
    declared = re.fullmatch(r"ghcr\.io/jdx/mise:([^-@]+)-debian@sha256:[0-9a-f]{64}", image)
    assert declared is not None, "the GitLab Mise image names the declared Mise release"
    prefix = "mise exec --locked -- "
    for role, name in GITLAB_MACOS_JOBS.items():
        job, linux = jobs[name], jobs[GITLAB_LINUX_JOBS[role]]
        check, install, *steps = _strings(job["before_script"])
        assert "mise --version" in check
        assert f'"{declared[1]} "*' in check, "the check admits the declared release alone"
        assert "exit 1" in check
        assert install == "mise install --locked"
        assert all(step.startswith(prefix) for step in (*steps, *_strings(job["script"])))
        linux_steps = _strings(linux["before_script"])
        assert [step.removeprefix(prefix) for step in steps] == linux_steps[-len(steps) :]
        assert not any("apt-get" in step for step in steps)
        assert [step.removeprefix(prefix) for step in _strings(job["script"])] == _strings(
            linux["script"]
        )


def test_gitlab_macos_job_keeps_its_tools_inside_the_project_cache() -> None:
    """Nothing the job runs comes from or goes to the runner account's home (D2)."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    default_cache = _mapping(_mapping(gitlab["default"])["cache"])
    for name in GITLAB_MACOS_JOBS.values():
        job = _gitlab_jobs()[name]
        variables = _mapping(job["variables"])
        cache = _mapping(job["cache"])
        paths = _strings(cache["paths"])
        assert set(_strings(default_cache["paths"])) <= set(paths)
        assert cache["key"] == f"{default_cache['key']}-$PYTHON_VERSION"
        for variable in (
            "UV_CACHE_DIR",
            "UV_PYTHON_INSTALL_DIR",
            "MISE_DATA_DIR",
            "MISE_CACHE_DIR",
            "MISE_STATE_DIR",
        ):
            value = _string(variables[variable])
            assert value.startswith("$CI_PROJECT_DIR/"), variable
            relative = value.removeprefix("$CI_PROJECT_DIR/") + "/"
            assert any(relative.startswith(path) for path in paths), variable


def test_every_other_gitlab_job_stays_on_its_linux_runners() -> None:
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    default = _mapping(gitlab["default"])
    linux_roles = {
        tuple(_strings(default["tags"])),
        tuple(_strings(_mapping(gitlab["commit-admission"])["tags"])),
    }
    others = {
        name: job for name, job in _gitlab_jobs().items() if name not in GITLAB_MACOS_JOBS.values()
    }
    assert others
    for name, job in others.items():
        assert "inherit" not in job, name
        assert "image" in job or "image" in default, name
        assert tuple(_strings(job.get("tags", default["tags"]))) in linux_roles, name
    macos_tags = {
        tag for name in GITLAB_MACOS_JOBS.values() for tag in _strings(_gitlab_jobs()[name]["tags"])
    }
    assert not macos_tags & {tag for role in linux_roles for tag in role}
