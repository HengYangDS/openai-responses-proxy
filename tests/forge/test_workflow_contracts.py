"""Portable contracts for GitHub verification and release workflows."""

import importlib
import json
import re
import shlex
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from tests.quality.fixtures import git
from tools.ci.project import MODEL
from tools.ci.project import reconcile
from tools.ci.project import render
from tools.git_environment import isolated_config_environment
from tools.quality.python import source_manifest

ROOT = Path(__file__).resolve().parents[2]
GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


def _load_yaml(path: Path) -> dict[str, object]:
    """Load one workflow while preserving GitHub's literal ``on`` key."""

    class WorkflowLoader(yaml.SafeLoader):
        pass

    for key, resolvers in tuple(WorkflowLoader.yaml_implicit_resolvers.items()):
        WorkflowLoader.yaml_implicit_resolvers[key] = [
            resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
        ]
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=WorkflowLoader)
    assert isinstance(data, dict)
    assert all(isinstance(key, str) for key in data)
    return {str(key): value for key, value in data.items()}


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow one parsed workflow mapping for typed contract assertions."""
    assert isinstance(value, Mapping)
    assert all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> list[object]:
    """Narrow one parsed workflow sequence."""
    assert isinstance(value, list)
    return list(value)


def _strings(value: object) -> list[str]:
    """Narrow one parsed workflow string sequence."""
    values = _sequence(value)
    assert all(isinstance(item, str) for item in values)
    return [item for item in values if isinstance(item, str)]


def _string(value: object) -> str:
    """Narrow one parsed workflow scalar to a string."""
    assert isinstance(value, str)
    return value


@pytest.mark.repository_toolchain
def test_forge_workflows_are_generated_from_one_declarative_graph() -> None:
    """Keep Forge syntax as projections, never independent topology owners."""
    governance = (ROOT / "tools" / "quality" / "governance.py").read_text(encoding="utf-8")
    assert 'sys.executable, "-m", "tools.ci.project"' in governance
    assert MODEL == ROOT / ".config/ci"
    assert reconcile(write=False) == ()


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


@pytest.mark.repository_toolchain
def test_github_actions_consume_one_immutable_toolchain_catalog() -> None:
    """Keep action identity and revision in one CUE-owned declaration."""
    catalog = [
        _string(value)
        for value in _mapping(yaml.safe_load(render("#Toolchains.githubActions"))).values()
    ]
    assert catalog
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action) for action in catalog)
    assert len({action.split("@", 1)[0] for action in catalog}) == len(catalog)
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    projected = {
        _string(step["uses"])
        for job in jobs.values()
        for raw_step in _sequence(_mapping(job)["steps"])
        if "uses" in (step := _mapping(raw_step))
    }
    assert projected == set(catalog)


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


def test_native_asset_jobs_install_the_product_before_loading_noxfile() -> None:
    """Make the source package importable while Nox loads its release sessions."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    expected = {
        "native-assets": "uv sync --locked --group quality",
        "native-linux": (
            "cd /workspace && uv sync --locked --group quality "
            "--python python --no-python-downloads"
        ),
        "native-linux-lifecycle": "uv sync --locked --group quality",
    }
    for job_id, command in expected.items():
        steps = _sequence(_mapping(jobs[job_id])["steps"])
        install = next(
            _mapping(step)
            for step in steps
            if _mapping(step).get("name") == "Install the locked release tool environment"
        )
        assert install["run"] == command

    linux_steps = _sequence(_mapping(jobs["native-linux"])["steps"])
    linux_build = next(
        _mapping(step)
        for step in linux_steps
        if _mapping(step).get("name") == "Build the native release asset"
    )
    assert "nox -s release_asset" in _string(linux_build["run"])
    assert "nox -s release --" not in _string(linux_build["run"])


def test_linux_release_source_preserves_tracked_package_inventory(
    tmp_path: Path,
) -> None:
    """Materialize the selected commit with the native inventory the build consumes."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    steps = _sequence(_mapping(jobs["native-linux"])["steps"])
    materialize = next(
        _mapping(step)
        for step in steps
        if _mapping(step).get("name") == "Materialize the canonical release source root"
    )
    retire = next(
        _mapping(step)
        for step in steps
        if _mapping(step).get("name") == "Retire the canonical release source root"
    )
    assert retire["if"] == "always() && steps.release-source.outcome == 'success'"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "--initial-branch=fixture-root")
    package = checkout / "src/openai_responses_proxy"
    package.mkdir(parents=True)
    source = package / "__init__.py"
    source.write_text('"""Selected release source."""\n', encoding="utf-8")
    git(checkout, "add", "--", "src")
    git(
        checkout,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--no-gpg-sign",
        "-qm",
        "Selected release source",
    )
    expected = source_manifest(checkout)
    selected_head = git(checkout, "rev-parse", "HEAD").stdout
    source.write_text('"""Uncommitted source."""\n', encoding="utf-8")
    (package / "local.py").write_text("pass\n", encoding="utf-8")
    destination = tmp_path / "release source"

    def execute(step: Mapping[str, object]) -> None:
        command = [
            argument.replace("$GITHUB_WORKSPACE", str(checkout)).replace(
                "/workspace", str(destination)
            )
            for argument in shlex.split(_string(step["run"]))
        ]
        subprocess.run(
            command,
            cwd=checkout,
            env=isolated_config_environment({"GITHUB_WORKSPACE": str(checkout)}),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
        )

    try:
        execute(materialize)
        assert source_manifest(destination) == expected
        assert git(destination, "rev-parse", "HEAD").stdout == selected_head
        assert not (destination / "src/openai_responses_proxy/local.py").exists()
    finally:
        if (destination / ".git").is_file():
            execute(retire)
    assert not destination.exists()


def test_linux_asset_build_and_native_lifecycle_have_distinct_execution_hosts() -> None:
    """Keep reproducible Linux construction separate from real systemd acceptance."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    build = _mapping(jobs["native-linux"])
    lifecycle = _mapping(jobs["native-linux-lifecycle"])

    assert build["container"] == "${{ needs.python-matrix.outputs.linux-release-image }}"
    assert "container" not in lifecycle
    assert lifecycle["runs-on"] == "ubuntu-24.04"
    assert lifecycle["needs"] == ["python-matrix", "native-linux"]
    lifecycle_steps = _sequence(lifecycle["steps"])
    assert any(
        _mapping(step).get("name") == "Prove the native Linux service lifecycle"
        and _mapping(step).get("run")
        == (
            "uv run --locked --no-sync python -m pytest -q --rootdir . "
            "-c .config/checks/pytest/pytest.toml tests/release/test_native_lifecycle.py"
        )
        for step in lifecycle_steps
    )
    assert any(
        _string(_mapping(step).get("uses", "")).split("@", 1)[0] == "actions/download-artifact"
        and _mapping(_mapping(step)["with"])["name"] == "native-linux-x86_64"
        for step in lifecycle_steps
    )
    assert any(
        _mapping(step).get("name") == "Materialize the Linux executable" for step in lifecycle_steps
    )
    lifecycle_command = next(
        _mapping(step)
        for step in lifecycle_steps
        if _mapping(step).get("name") == "Prove the native Linux service lifecycle"
    )
    assert _mapping(lifecycle_command["env"])["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"] == (
        "${{ runner.temp }}/native-linux/runtime/bin/openai-responses-proxy"
    )
    assert _mapping(lifecycle_command["env"])["OPENAI_RESPONSES_PROXY_NATIVE_BUNDLE"] == (
        "${{ runner.temp }}/native-linux/runtime/bin"
    )


def test_release_compatibility_runs_real_published_upgrade_on_each_platform() -> None:
    """Exercise one authentic published predecessor before every accepted patch."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    compatibility = _mapping(jobs["release-compatibility"])
    assert compatibility["runs-on"] == "${{ matrix.runner }}"
    assert compatibility["needs"] == "python-matrix"
    matrix = _sequence(_mapping(_mapping(compatibility["strategy"])["matrix"])["include"])
    assert matrix == [
        {
            "platform": "macos-arm64",
            "runner": "macos-26",
        },
        {
            "platform": "windows-x86_64",
            "runner": "windows-2025",
        },
        {
            "platform": "linux-x86_64",
            "runner": "ubuntu-24.04",
        },
    ]
    steps = tuple(_mapping(step) for step in _sequence(compatibility["steps"]))
    download = next(
        step for step in steps if step.get("name") == "Download the published predecessor release"
    )
    assert _mapping(download["env"])["GH_TOKEN"] == "${{ github.token }}"
    assert (
        'gh release download "${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG }}"'
        in _string(download["run"])
    )
    assert '"$OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG"' not in _string(download["run"])
    assert "--pattern" in _string(download["run"])
    assert "--repo" not in _string(download["run"])
    predecessor = next(
        step for step in steps if step.get("name") == "Resolve the exact published predecessor"
    )
    assert _mapping(predecessor["env"])["GH_TOKEN"] == "${{ github.token }}"
    assert predecessor["run"] == (
        "uv run --locked --no-sync python -m tools.release.publication predecessor "
        '--repository "${{ github.repository }}" --candidate-version "$(cat VERSION)" '
        '--github-environment "${{ github.env }}"'
    )
    trust = next(
        step for step in steps if step.get("name") == "Materialize the release trust anchor"
    )
    assert _mapping(trust["env"]) == {
        "PREVIOUS_RELEASE_ASSET_TRUST": "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}",
        "RELEASE_ASSET_TRUST": "${{ vars.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}",
    }
    proof = next(
        step
        for step in steps
        if step.get("name") == "Prove published predecessor upgrade and rollback"
    )
    assert (
        _mapping(proof["env"])["OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR"]
        == "${{ runner.temp }}/previous-release-asset-trust"
    )
    assert proof["run"] == "uv run --locked --no-sync nox -s release_compatibility"


def test_published_release_bytes_run_the_full_native_journey_on_each_platform() -> None:
    """Re-download the released candidate before claiming platform acceptance."""
    workflow = _load_yaml(ROOT / ".github/workflows/verify.yml")
    triggers = _mapping(workflow["on"])
    assert _mapping(triggers["release"])["types"] == ["published"]
    release_input = _mapping(_mapping(triggers["workflow_dispatch"])["inputs"])["release_tag"]
    assert _mapping(release_input) == {
        "description": "Published vMAJOR.MINOR.PATCH tag to verify",
        "required": "true",
        "type": "string",
    }

    job = _mapping(_mapping(workflow["jobs"])["published-release-compatibility"])
    assert job["if"] == (
        "github.event_name == 'release' || github.event_name == 'workflow_dispatch'"
    )
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert _sequence(_mapping(_mapping(job["strategy"])["matrix"])["include"]) == [
        {"platform": "macos-arm64", "runner": "macos-26"},
        {"platform": "windows-x86_64", "runner": "windows-2025"},
        {"platform": "linux-x86_64", "runner": "ubuntu-24.04"},
    ]
    steps = tuple(_mapping(step) for step in _sequence(job["steps"]))
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert _mapping(checkout["with"])["ref"] == (
        "${{ github.event_name == 'release' && github.event.release.tag_name || github.sha }}"
    )
    commands = "\n".join(_string(step.get("run", "")) for step in steps)
    for token in (
        'git diff --exit-code "${{ github.event.release.tag_name || inputs.release_tag }}^{commit}" HEAD -- '
        "VERSION pyproject.toml uv.lock src/openai_responses_proxy",
        'gh release download "${{ github.event.release.tag_name || inputs.release_tag }}"',
        "OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET",
        'nox -s published_release_compatibility -- --basetemp="${{ runner.temp }}/proxy-test"',
    ):
        assert token in commands
    assert any(
        step.get("name") == "Start the runner user systemd manager"
        and step.get("if") == "matrix.platform == 'linux-x86_64'"
        for step in steps
    )


def test_python_matrix_output_comes_from_the_repository_ssot(tmp_path: Path) -> None:
    module = importlib.import_module("tools.quality.python_matrix")
    versions = tmp_path / ".python-versions"
    metadata = tmp_path / "pyproject.toml"
    output = tmp_path / "github-output"
    versions.write_text("3.12\n3.13\n3.14\n", encoding="ascii")
    release = tmp_path / ".python-release"
    release.write_text("3.14.7\n", encoding="ascii")
    metadata.write_text(
        '[project]\nname = "openai-responses-proxy"\n'
        '[tool.openai-responses-proxy]\nlinux-release-image = "python:3.14.7-bookworm@sha256:'
        + "a" * 64
        + '"\n',
        encoding="ascii",
    )

    module.write(versions=versions, release=release, metadata=metadata, output=output)

    assert output.read_text(encoding="utf-8") == (
        'value=["3.12", "3.13", "3.14"]\nfloor=3.12\nlatest=3.14\nrelease=3.14.7\n'
        f"linux-release-image=python:3.14.7-bookworm@sha256:{'a' * 64}\n"
    )


@pytest.mark.parametrize(
    ("release_value", "image_version", "message"),
    [
        ("3.14", "3.14.7", "native release Python is unavailable or invalid"),
        (
            "not.a.version",
            "not.a.version",
            "native release Python is unavailable or invalid",
        ),
        ("3.14.7", "3.14.6", "Linux release runtime is unavailable or mutable"),
    ],
)
def test_python_matrix_rejects_invalid_or_mismatched_release_runtime(
    tmp_path: Path, release_value: str, image_version: str, message: str
) -> None:
    module = importlib.import_module("tools.quality.python_matrix")
    versions = tmp_path / ".python-versions"
    release = tmp_path / ".python-release"
    metadata = tmp_path / "pyproject.toml"
    versions.write_text("3.12\n3.13\n3.14\n", encoding="ascii")
    release.write_text(f"{release_value}\n", encoding="ascii")
    metadata.write_text(
        '[project]\nname = "openai-responses-proxy"\n'
        '[tool.openai-responses-proxy]\nlinux-release-image = "python:'
        f"{image_version}-bookworm@sha256:{'a' * 64}"
        '"\n',
        encoding="ascii",
    )

    with pytest.raises(ValueError, match=message):
        module.write(
            versions=versions,
            release=release,
            metadata=metadata,
            output=tmp_path / "github-output",
        )


def test_native_release_runtime_is_exact_and_platform_independent() -> None:
    native_runtime = (ROOT / ".python-release").read_text(encoding="ascii").strip()
    image = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "openai-responses-proxy"
    ]["linux-release-image"]
    image_version = re.search(r"python:(\d+\.\d+\.\d+)-", image)
    nox_source = (ROOT / "noxfile.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    native_job = workflow.split("\n  native-assets:", 1)[1].split("\n  native-linux:", 1)[0]

    assert re.fullmatch(r"\d+\.\d+\.\d+", native_runtime)
    assert image_version is not None
    assert native_runtime == image_version.group(1)
    assert 'if platform.system() != "Linux":' not in nox_source
    assert "@nox.session(python=RELEASE_PYTHON)" in nox_source
    assert "python-version: ${{ needs.python-matrix.outputs.release }}" in native_job


def test_native_bundle_is_built_and_signed_once_in_release_graph() -> None:
    """Keep native construction and product signing in one authoritative workflow."""
    verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    assert set(_strings(_mapping(jobs["release-assets"])["needs"])) == {
        "python-matrix",
        "tag-metadata",
        "native-assets",
        "native-linux",
        "native-linux-lifecycle",
    }
    assert verify.count("uv run --locked --no-sync python -m tools.release.artifact assemble") == 1
    assert verify.count("--sign") == 1
    assert "container: ${{ needs.python-matrix.outputs.linux-release-image }}" in verify


def test_gitlab_verification_bootstrap_is_bounded_and_cached() -> None:
    """Start verification from immutable UV/Python executors, not pip bootstrap."""
    text = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    default = _mapping(gitlab["default"])
    default_image = _mapping(default["image"])
    quality = _mapping(gitlab["verify-python-quality"])

    assert default_image == {"name": "$UV_PYTHON_LATEST_IMAGE"}
    variables = _mapping(gitlab["variables"])
    assert variables["UV_CACHE_DIR"] == "$CI_PROJECT_DIR/.cache/uv"
    assert variables["UV_PYTHON_INSTALL_DIR"] == "$CI_PROJECT_DIR/.cache/uv/python"
    assert variables["OPENAI_RESPONSES_PROXY_CI_TARGET"] == "linux-arm64"
    assert _mapping(default["cache"])["key"] == "uv-$OPENAI_RESPONSES_PROXY_CI_TARGET"
    assert _mapping(default["cache"])["paths"] == [".cache/uv/"]
    assert _mapping(quality["image"]) == {"name": "$UV_PYTHON_FLOOR_IMAGE"}
    assert "python -m pip install" not in text
    assert "uv sync --locked --group quality --python python --no-python-downloads" in text
    assert "apt-get install -qq -y --no-install-recommends binutils" in _strings(
        quality["before_script"]
    )
    python = _mapping(gitlab["verify-python"])
    assert "uv python install --no-bin $PYTHON_VERSION" in _strings(python["before_script"])


def test_gitlab_source_job_uses_one_locked_toolchain() -> None:
    """Run source policy from the immutable mise image and repository lock."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    source = _mapping(gitlab["source-and-governance"])

    image = _mapping(source["image"])
    assert re.fullmatch(r"ghcr.io/jdx/mise@sha256:[0-9a-f]{64}", _string(image["name"]))
    assert image["entrypoint"] == [""]
    assert _mapping(source["variables"]) == {
        "GIT_DEPTH": "0",
        "MISE_ENABLE_TOOLS": (
            "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,"
            "github:rhysd/actionlint,github:lycheeverse/lychee"
        ),
    }
    assert source["before_script"] == [
        "mise install --locked",
        "npm ci --ignore-scripts",
        "npm audit signatures",
        "git fetch --tags --force --prune --prune-tags origin",
        (
            "mise exec --locked -- uv sync --locked --group quality "
            "--python python --no-python-downloads"
        ),
    ]
    (source_command,) = _strings(source["script"])
    assert source_command.endswith(
        "mise exec --locked -- uv run --locked --no-sync --python python "
        "--no-python-downloads python -m tools.quality.governance --online-links"
    )
    assert _string(_mapping(source["variables"])["MISE_ENABLE_TOOLS"]).startswith("python,uv,")


def test_node_repository_tools_have_one_locked_owner() -> None:
    """Install Node tools from the repository lock, never the mise npm backend."""
    package = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    dev_dependencies = package["packages"][""]["devDependencies"]
    manifest = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert dev_dependencies == manifest["devDependencies"]
    assert set(dev_dependencies) == {"@fission-ai/openspec", "prettier"}
    for name, version in dev_dependencies.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        assert package["packages"][f"node_modules/{name}"]["version"] == version

    mise = tomllib.loads((ROOT / ".config/mise/config.toml").read_text(encoding="utf-8"))
    assert all(not name.startswith("npm:") for name in mise["tools"])

    github = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])[
        "source-and-governance"
    ]
    github_steps = tuple(_mapping(step) for step in _sequence(_mapping(github)["steps"]))
    github_script = "\n".join(str(step.get("run", "")) for step in github_steps)
    assert "npm ci --ignore-scripts" in github_script
    assert "npm audit signatures" in github_script

    tag = _mapping(
        _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])["tag-metadata"]
    )
    tag_steps = tuple(_mapping(step) for step in _sequence(tag["steps"]))
    tag_script = "\n".join(str(step.get("run", "")) for step in tag_steps)
    assert "npm ci --ignore-scripts" in tag_script
    assert "npm audit signatures" in tag_script


def test_github_python_quality_installs_its_declared_projection_toolchain() -> None:
    """Provision every repository-locked tool consumed by the quality owner."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    quality = _mapping(jobs["python-quality"])
    steps = tuple(_mapping(step) for step in _sequence(quality["steps"]))
    mise = next(step for step in steps if str(step.get("uses", "")).startswith("jdx/mise-action@"))

    assert re.fullmatch(r"jdx/mise-action@[0-9a-f]{40}", _string(mise["uses"]))
    mise_actions = {
        str(step["uses"])
        for job in jobs.values()
        for raw_step in _sequence(_mapping(job)["steps"])
        if str((step := _mapping(raw_step)).get("uses", "")).startswith("jdx/mise-action@")
    }
    assert mise_actions == {mise["uses"]}
    assert _mapping(mise["with"]) == {
        "install": "true",
        "cache": "true",
    }


def _assert_github_required_tokens(text: str) -> None:
    required = [
        "name: Verify",
        "push:",
        "permissions:\n  contents: read",
        'GIT_CONFIG_COUNT: "1"',
        "GIT_CONFIG_KEY_0: init.defaultBranch",
        "GIT_CONFIG_VALUE_0: main",
        "runs-on: macos-26",
        "runs-on: ubuntu-24.04",
        "actions/checkout@",
        "uv run --locked --no-sync python -m tools.quality.python_matrix",
        "python-version: ${{ fromJSON(needs.python-matrix.outputs.versions) }}",
        'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"',
        "python-windows:",
        "windows-2025",
        "actions/setup-python@",
        "fetch-tags: true",
        "if: github.event_name == 'push' && github.ref_type == 'tag'",
        "python -m tools.quality.governance --online-links",
        'uv run --locked --no-sync python -m tools.release.metadata --tag "$GITHUB_REF_NAME"',
        "uv run --locked --no-sync python -m tools.release.metadata",
        "python-quality:",
        "astral-sh/setup-uv@",
        "uv run --locked --group quality nox -s quality",
        "python -m pytest -q --rootdir . -c .config/checks/pytest/pytest.toml "
        "tests/quality/test_contract.py tests/forge/test_workflow_contracts.py",
        "tests/release/publication",
        "native-assets:",
        "name: Native asset (${{ matrix.platform }})",
        "platform: macos-arm64",
        "platform: windows-x86_64",
        "native-linux:",
        "name: Native asset (linux-x86_64)",
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
        'printf \'%s\\n\' "$RELEASE_ASSET_SIGNING_KEY_TEXT" > "$RUNNER_TEMP/release-asset-signing-key"',
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
    windows_start = text.index("\n  python-windows:")
    governance_start = text.index("\n  accepted-source:")
    mac_block = text[mac_start:windows_start]
    windows_block = text[windows_start:governance_start]
    setup_uv = "astral-sh/setup-uv@"
    if mac_block.count(setup_uv) != 1:
        raise AssertionError("macOS Python matrix must use exactly one setup-uv action")
    test_owner = 'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"'
    if test_owner not in mac_block:
        raise AssertionError(f"macOS Python matrix must run {test_owner}")
    for token in (
        "runs-on: windows-2025",
        "python-version: ${{ fromJSON(needs.python-matrix.outputs.versions) }}",
        "actions/checkout@",
        "actions/setup-python@",
        'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"',
    ):
        if token not in windows_block:
            raise AssertionError(f"Windows Python matrix must contain {token!r}")
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    host_native_jobs = (
        "source-and-governance",
        "python",
        "python-windows",
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
    if windows_block.count("actions/setup-python@") != 1:
        raise AssertionError("Windows verification must use exactly one pinned setup-python action")
    if windows_block.count(setup_uv) != 1:
        raise AssertionError("Windows Python matrix must use exactly one setup-uv action")
    quality_start = text.index("\n  python-quality:")
    quality_end = text.index("\n  native-assets:", quality_start)
    quality_block = text[quality_start:quality_end]
    for token in ("fetch-depth: 0", "fetch-tags: true"):
        if token not in quality_block:
            raise AssertionError(f"quality checkout must contain {token!r}")
    for patch_pin in ("3.12.", "3.13.", "3.14."):
        if patch_pin in text:
            raise AssertionError(
                f"GitHub workflows must select supported Python lines, not patch releases: {patch_pin!r}"
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
    windows_start = text.index("\n  python-windows:")
    governance_start = text.index("\n  accepted-source:")
    windows_block = text[windows_start:governance_start]
    native_start = text.index("\n  native-assets:")
    native_end = text.index("\n  release-assets:", native_start)
    native_block = text[native_start:native_end]
    if "shell: bash" in native_block or "set -euo pipefail" in native_block:
        raise AssertionError("native asset builds must use each runner's native command shell")
    if "shell:" in windows_block or re.search(r"(?:^|\s)\S+\.sh(?:\s|$)", windows_block):
        raise AssertionError("Windows verification must not depend on Bash or POSIX shell scripts")
    if "secrets:" in windows_block or "permissions:" in windows_block:
        raise AssertionError(
            "Windows verification must inherit the read-only, secret-free workflow contract"
        )
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
    print("GitHub Actions verification contract: OK")


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


def test_commit_event_inputs_reach_each_governance_context() -> None:
    github = _load_yaml(ROOT / ".github/workflows/verify.yml")
    jobs = _mapping(github["jobs"])
    for name in (
        "source-and-governance",
        "accepted-source",
        "promotion",
        "tag-metadata",
    ):
        steps = [_mapping(step) for step in _sequence(_mapping(jobs[name])["steps"])]
        checks = [
            step
            for step in steps
            if "tools.quality.governance" in str(step.get("run", ""))
            or "tools.quality.repository" in str(step.get("run", ""))
        ]
        assert checks, name
        for step in checks:
            environment = _mapping(step["env"])
            assert environment["OPENAI_RESPONSES_PROXY_COMMIT_HEAD"] == (
                "${{ github.event.pull_request.head.sha || github.sha }}"
            )
            assert environment["OPENAI_RESPONSES_PROXY_COMMIT_BASE"] == (
                "${{ github.ref_type == 'tag' && github.sha || github.event.pull_request.base.sha || github.event.before }}"
            )
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    for name in (
        "source-and-governance",
        "verify-accepted-source",
        "verify-promotion",
        "verify-release-tag",
    ):
        commands = _strings(_mapping(gitlab[name])["script"])
        checks = [
            command
            for command in commands
            if "tools.quality.governance" in command or "tools.quality.repository" in command
        ]
        assert checks, name
        for command in checks:
            assert "OPENAI_RESPONSES_PROXY_COMMIT_HEAD" in command
            assert "OPENAI_RESPONSES_PROXY_COMMIT_BASE" in command
            assert "CI_MERGE_REQUEST_DIFF_BASE_SHA" in command
            assert "CI_COMMIT_BEFORE_SHA" in command
            assert "CI_COMMIT_TAG" in command


@pytest.mark.parametrize("event", ["review", "push", "api", "tag"])
def test_gitlab_commit_projection_executes_native_event_values(event, monkeypatch) -> None:
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    command = _strings(_mapping(gitlab["verify-accepted-source"])["script"])[-1]
    prefix = command.split("uv run", 1)[0]
    head, base, review = "a" * 40, "b" * 40, "c" * 40
    zero = "0" * 40
    for name, value in {
        "CI_COMMIT_SHA": head,
        "CI_COMMIT_BEFORE_SHA": zero if event == "api" else base,
        "CI_MERGE_REQUEST_DIFF_BASE_SHA": review if event == "review" else "",
        "CI_PIPELINE_SOURCE": "merge_request_event" if event == "review" else event,
        "CI_COMMIT_TAG": "v4.0.3" if event == "tag" else "",
    }.items():
        monkeypatch.setenv(name, value)
    result = subprocess.run(
        [
            "sh",
            "-c",
            prefix
            + 'printf "%s\\n%s\\n" "$OPENAI_RESPONSES_PROXY_COMMIT_BASE" "$OPENAI_RESPONSES_PROXY_COMMIT_HEAD"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        {"review": review, "push": base, "api": zero, "tag": head}[event],
        head,
    ]
