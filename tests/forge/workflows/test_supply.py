"""Locked runner and repository-tool supply in each Forge projection."""

import json
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


def test_uv_consumers_share_the_integrity_locked_version() -> None:
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())
    lock = tomllib.loads((ROOT / ".config/mise/mise.lock").read_text())
    uv_config = tomllib.loads((ROOT / ".config/mise/uv.toml").read_text())
    version = toolchain["tools"]["uv"]
    (locked,) = lock["tools"]["uv"]
    assert locked["version"] == version
    assert locked["specifiers"] == [version]
    assert uv_config["required-version"] == f"=={version}"
    for name, platform in locked.items():
        if not name.startswith("platforms."):
            continue
        assert f"/releases/download/{version}/" in platform["url"]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", platform["checksum"])
        assert platform["provenance"] == "github-attestations"
    variables = _mapping(_load_yaml(ROOT / ".gitlab-ci.yml")["variables"])
    for name, python in (("FLOOR", "3.12"), ("LATEST", "3.14")):
        assert re.fullmatch(
            rf"ghcr.io/astral-sh/uv:{re.escape(version)}-python{re.escape(python)}"
            r"-trixie-slim@sha256:[0-9a-f]{64}",
            _string(variables[f"UV_PYTHON_{name}_IMAGE"]),
        )


def test_each_lock_holds_only_the_platforms_that_its_tool_declares() -> None:
    """Mise's and uv's own settings name the platforms that their locks hold."""
    settings = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())["settings"]
    lock = tomllib.loads((ROOT / ".config/mise/mise.lock").read_text())
    declared = set(settings["lockfile_platforms"])
    locked = {
        name.removeprefix("platforms.")
        for entries in lock["tools"].values()
        for entry in entries
        for name in entry
        if name.startswith("platforms.")
    }
    assert locked == declared
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert len(uv_lock["supported-markers"]) == len(project["tool"]["uv"]["environments"])
    assert uv_lock["resolution-markers"] == uv_lock["supported-markers"]


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


@pytest.mark.repository_toolchain
def test_both_peers_install_the_mise_release_of_the_gitlab_image() -> None:
    """GitHub installs the Mise release that GitLab's image carries, so none arrives unannounced."""
    source = _mapping(_load_yaml(ROOT / ".gitlab-ci.yml")["source-and-governance"])
    image = _string(_mapping(source["image"])["name"])
    release = re.fullmatch(r"ghcr.io/jdx/mise:(\d{4}\.\d+\.\d+)-debian@sha256:[0-9a-f]{64}", image)
    assert release is not None, image
    action = _string(_mapping(yaml.safe_load(render("#Toolchains.githubActions")))["mise"]).split(
        "@", 1
    )[0]
    steps = [
        step
        for job in _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"]).values()
        for raw_step in _sequence(_mapping(job)["steps"])
        if _string((step := _mapping(raw_step)).get("uses", "")).split("@", 1)[0] == action
    ]
    assert steps
    assert {str(_mapping(step["with"]).get("version")) for step in steps} == {release.group(1)}


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
    python = _mapping(gitlab["verify-python"])
    assert "uv python install --no-bin $PYTHON_VERSION" in _strings(python["before_script"])


def test_gitlab_bootstrapped_jobs_install_the_native_packaging_tools() -> None:
    """Give every apt-bootstrapped GitLab job the binutils whose objdump PyInstaller needs."""
    bootstrapped: dict[str, list[str]] = {}
    for name, raw in _mapping(_load_yaml(ROOT / ".gitlab-ci.yml")).items():
        if isinstance(raw, dict):
            script = _strings(_mapping(raw).get("before_script", []))
            if "apt-get update -qq" in script:
                bootstrapped[name] = script
    expected = {"verify-performance", "verify-performance-accepted", "verify-python-quality"}
    assert expected <= set(bootstrapped)
    missing = [
        name
        for name, script in bootstrapped.items()
        if not any(
            line.startswith("apt-get install") and "binutils" in line.split() for line in script
        )
    ]
    assert missing == []


def test_gitlab_source_job_uses_one_locked_toolchain() -> None:
    """Run source policy from the immutable Debian mise image, whose shell GitLab needs."""
    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    source = _mapping(gitlab["source-and-governance"])
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())

    image = _mapping(source["image"])
    assert re.fullmatch(
        r"ghcr.io/jdx/mise:\d{4}\.\d+\.\d+-debian@sha256:[0-9a-f]{64}", _string(image["name"])
    )
    assert image["entrypoint"] == [""]
    variables = _mapping(source["variables"])
    assert set(variables) == {"GIT_DEPTH", "MISE_ENABLE_TOOLS", "UV_CONFIG_FILE", "UV_NO_CONFIG"}
    assert variables["UV_CONFIG_FILE"] == ".config/mise/uv.toml"
    assert variables["UV_NO_CONFIG"] == "false"
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
    """Install Node tools from the repository lock with the npm that Mise selects and locks."""
    package = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    dev_dependencies = package["packages"][""]["devDependencies"]
    manifest = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert dev_dependencies == manifest["devDependencies"]
    assert set(dev_dependencies) == {"@fission-ai/openspec", "prettier"}
    for name, version in dev_dependencies.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        assert package["packages"][f"node_modules/{name}"]["version"] == version

    # The package manager itself is the one tool of Mise's npm backend, under its own name.
    mise = tomllib.loads((ROOT / ".config/mise/config.toml").read_text(encoding="utf-8"))
    aliases = mise.get("tool_alias", {})
    backends = {name: aliases.get(name, name) for name in mise["tools"]}
    assert [name for name, backend in backends.items() if backend.startswith("npm:")] == ["npm"]
    assert backends["npm"] == "npm:npm"
    version = mise["tools"]["npm"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    lock = tomllib.loads((ROOT / ".config/mise/mise.lock").read_text(encoding="utf-8"))
    (locked,) = lock["tools"]["npm"]
    assert (locked["backend"], locked["version"]) == ("npm:npm", version)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", locked["aube"]["digest"])

    # Mise's lock folder holds exactly the locks that mise.lock names: a version left behind
    # by an update is refused as well as a missing one.
    named = {
        entry["aube"]["path"]
        for entries in lock["tools"].values()
        for entry in entries
        if "aube" in entry
    }
    held = {
        folder.relative_to(ROOT / ".config/mise").as_posix()
        for folder in (ROOT / ".config/mise/locks").glob("*/*")
        if folder.is_dir()
    }
    assert held == named


_MISE_ACTION = "jdx/mise-action@"


def _runs_npm(line: str) -> bool:
    """Whether a shell line starts npm, directly or through `mise exec`."""
    return re.match(r"\s*npm\s", line) is not None or " -- npm " in line


def _mise_selects_npm(*environments: object) -> bool:
    """Whether no environment leaves npm out of the tools that Mise provides."""
    for environment in environments:
        if not isinstance(environment, dict):
            continue
        enabled = environment.get("MISE_ENABLE_TOOLS")
        if enabled is not None and "npm" not in str(enabled).split(","):
            return False
        if "npm" in str(environment.get("MISE_DISABLE_TOOLS", "")).split(","):
            return False
    return True


def test_every_github_job_that_runs_npm_runs_the_selected_one() -> None:
    """Select jobs by what they run, and require Mise's npm before the first npm command."""
    selected = 0
    for workflow in (".github/workflows/verify.yml", ".github/workflows/admission.yml"):
        document = _mapping(_load_yaml(ROOT / workflow))
        for name, raw in _mapping(document["jobs"]).items():
            job = _mapping(raw)
            steps = tuple(_mapping(step) for step in _sequence(job.get("steps", [])))
            npm = [
                index
                for index, step in enumerate(steps)
                if any(_runs_npm(line) for line in str(step.get("run", "")).splitlines())
            ]
            if not npm:
                continue
            selected += 1
            uses = [str(step.get("uses", "")) for step in steps]
            mise = [index for index, used in enumerate(uses) if used.startswith(_MISE_ACTION)]
            assert mise, name
            assert mise[0] < npm[0], name
            options = _mapping(steps[mise[0]]["with"])
            assert str(options["install"]).lower() == "true", name
            assert "npm" in str(options.get("install_args", "npm")).split(), name
            assert not any(used.startswith("actions/setup-node@") for used in uses), name
            environments = (
                document.get("env"),
                job.get("env"),
                *(step.get("env") for step in steps),
            )
            assert _mise_selects_npm(*environments), name
    assert selected


def test_every_gitlab_job_that_runs_npm_runs_the_selected_one() -> None:
    """Select jobs by what they run, and require `mise install` before the first npm command."""
    pipeline = _mapping(_load_yaml(ROOT / ".gitlab-ci.yml"))
    selected = 0
    for name, raw in pipeline.items():
        if not isinstance(raw, dict) or not ({"script", "before_script"} & raw.keys()):
            continue
        job = _mapping(raw)
        lines = [
            line
            for key in ("before_script", "script")
            for entry in _strings(job.get(key, []))
            for line in entry.splitlines()
        ]
        npm = [index for index, line in enumerate(lines) if _runs_npm(line)]
        if not npm:
            continue
        selected += 1
        assert "mise install --locked" in lines[: npm[0]], name
        assert _mise_selects_npm(pipeline.get("variables"), job.get("variables")), name
    assert selected


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
    assert set(_mapping(mise["with"])) == {"version", "install", "cache"}
    assert _mapping(mise["with"])["install"] == "true"
    assert _mapping(mise["with"])["cache"] == "true"


@pytest.mark.repository_toolchain
def test_ethos_supplier_prepares_native_wheel_inputs_before_building() -> None:
    """Prepare native runtime and policy-tool bytes before the noneditable wheel build."""
    prepare = _strings(yaml.safe_load(render("#EthosSupply.prepare")))
    root = "build/runtime/ethos-source"
    native = (
        f"uv --directory {root} run --locked --no-sync "
        "python -B -m tools.ci.toolchain.native --root . --mise node vale"
    )
    build = (
        f"uv --directory {root} sync --locked --group dev --no-editable "
        "--no-build-isolation-package ethos"
    )
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
        (
            f"{prefix} run --locked --no-sync python -B -m tools.ci.toolchain.native "
            "--root . --mise node vale"
        ),
        (
            f"{prefix} run --locked --no-sync python -B -m tools.ci.toolchain.npm "
            "--seed ci --ignore-scripts"
        ),
        f"{prefix} sync --locked --group dev --no-editable --no-build-isolation-package ethos",
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
        encoding="locale",
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
