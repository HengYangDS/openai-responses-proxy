"""Locked runner and repository-tool supply in each Forge projection."""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Literal

import pytest
import yaml

from tests.forge.workflows.fixtures import _load_yaml
from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _string
from tests.forge.workflows.fixtures import _strings
from tests.quality.fixtures import ROOT
from tools.ci.project import render

GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


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
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())

    image = _mapping(source["image"])
    assert re.fullmatch(r"ghcr.io/jdx/mise@sha256:[0-9a-f]{64}", _string(image["name"]))
    assert image["entrypoint"] == [""]
    variables = _mapping(source["variables"])
    assert set(variables) == {"GIT_DEPTH", "MISE_ENABLE_TOOLS"}
    assert variables["GIT_DEPTH"] == "0"
    assert set(_string(variables["MISE_ENABLE_TOOLS"]).split(",")) <= toolchain["tools"].keys()
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
    assert _string(variables["MISE_ENABLE_TOOLS"]).startswith("python,uv,")


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


@pytest.mark.repository_toolchain
def test_ethos_supplier_prepares_native_wheel_inputs_before_building() -> None:
    """Prepare native runtime and policy-tool bytes before the noneditable wheel build."""
    prepare = _strings(yaml.safe_load(render("#EthosSupply.prepare")))
    root = "build/runtime/ethos-source"
    native = (
        f"uv --directory {root} run --locked --no-sync "
        "python -B -m tools.ci.toolchain.native --root . node vale"
    )
    build = f"uv --directory {root} sync --locked --group dev --no-editable"
    assert prepare.count(native) == 1
    assert prepare.index(native) < prepare.index(build)


def test_commit_admission_uses_target_native_locked_ethos_supply() -> None:
    github = _load_yaml(ROOT / ".github/workflows/verify.yml")
    job = _mapping(_mapping(github["jobs"])["commit-admission"])
    steps = tuple(_mapping(step) for step in _sequence(job["steps"]))
    refresh = next(
        step
        for step in steps
        if step.get("name") == "Refresh native ETHOS Linux prerequisite metadata"
    )
    assert refresh["run"] == "sudo apt-get update -qq"
    prerequisites = next(
        step for step in steps if step.get("name") == "Install native ETHOS Linux prerequisites"
    )
    install = _string(prerequisites["run"])
    assert install.startswith("sudo apt-get install -qq -y --no-install-recommends ")
    assert {"curl", "ca-certificates", "libatomic1"} <= set(install.split())
    checkout = [
        _string(step["run"])
        for step in steps
        if step.get("name") == "Select the exact native ETHOS source"
    ]
    root = "build/runtime/ethos-source"
    assert len(checkout) == 3
    assert checkout[0].startswith("git clone --filter=blob:none --no-checkout https://")
    assert checkout[0].endswith(f" {root}")
    assert re.fullmatch(rf"git -C {root} checkout --detach [0-9a-f]{{40}}", checkout[1])
    assert re.fullmatch(rf"git -C {root} diff --exit-code [0-9a-f]{{40}} HEAD --", checkout[2])
    prepare = [
        _string(step["run"])
        for step in steps
        if "Prepare locked native ETHOS" in str(step.get("name", ""))
    ]
    prefix = f"uv --directory {root}"
    assert prepare[:5] == [
        f"{prefix} sync --locked --no-install-project --group dev",
        f"{prefix} run --locked --no-sync python -B -m tools.ci.toolchain.native --root . node vale",
        f"{prefix} run --locked --no-sync python -B -m tools.ci.toolchain.npm --seed ci --ignore-scripts",
        f"{prefix} sync --locked --group dev --no-editable",
        f"{prefix} run --locked --no-sync python -B -I -m ethos.cli --version",
    ]
    assert len(prepare) == 6
    assert prepare[-1].startswith(f"{prefix} run --locked --no-sync python -B -c ")
    assert "bind_commit_trust_material" in prepare[-1]
    assert _mapping(job["env"])["ETHOS_COMMIT_ALLOWED_SIGNERS"] == (
        "${{ vars.ETHOS_COMMIT_ALLOWED_SIGNERS }}"
    )
    assert ".whl" not in "\n".join((*checkout, *prepare))

    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    review = _mapping(gitlab["commit-admission"])
    accepted = _mapping(gitlab["commit-admission-accepted"])
    assert review["tags"] == ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_REVIEW_RUNNER_TAG"]
    assert accepted["tags"] == ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_RUNNER_TAG"]
    assert review["before_script"] == accepted["before_script"]
    before = _strings(review["before_script"])
    assert install.removeprefix("sudo ") in before
    assert before[-len(checkout + prepare) :] == checkout + prepare


@pytest.mark.repository_toolchain
@pytest.mark.skipif(os.name == "nt", reason="Both selected Forge jobs execute POSIX sh")
@pytest.mark.parametrize("peer", ["github", "gitlab"])
@pytest.mark.parametrize(
    ("trust", "expected_exit"),
    [
        (None, 1),
        ("", 1),
        (" \t\n", 1),
        ("\r\n", 1),
        ("public-trust-fixture", 0),
        (" \tpublic-trust-fixture\r\n", 0),
        ("$(unavailable-command)", 0),
    ],
)
def test_commit_trust_presence_precedes_supply_commands(
    tmp_path: Path,
    peer: Literal["github", "gitlab"],
    trust: str | None,
    expected_exit: int,
) -> None:
    if peer == "github":
        workflow = _mapping(yaml.safe_load(render("#SourceJobs")))
        job = _mapping(workflow["commit-admission"])
        steps = tuple(_mapping(step) for step in _sequence(job["steps"]))
        commands = [_string(step["run"]) for step in steps if "run" in step]
    else:
        workflow = _mapping(yaml.safe_load(render("gitlab")))
        commands = _strings(_mapping(workflow["commit-admission"])["before_script"])
    environment = {"PATH": "", "HOME": str(tmp_path), "LC_ALL": "C"}
    if trust is not None:
        environment["ETHOS_COMMIT_ALLOWED_SIGNERS"] = trust
    # Empty PATH traps any attempted supply command; nonempty input only proves presence.
    result = subprocess.run(
        ("/bin/sh", "-eu", "-c", commands[0] if expected_exit == 0 else "\n".join(commands)),
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == expected_exit, result.stderr
    assert not tuple(tmp_path.iterdir())
    assert not result.stdout
    assert not result.stderr


@pytest.mark.repository_toolchain
def test_commit_trust_preflight_uses_one_native_command() -> None:
    command = _string(yaml.safe_load(render("#CommitTrustPreflight")))
    assert command.startswith("test ")
    assert "\n" not in command
    assert "ETHOS_COMMIT_ALLOWED_SIGNERS" in command
