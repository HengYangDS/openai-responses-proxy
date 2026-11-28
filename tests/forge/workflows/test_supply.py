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
    """Give every system-bootstrapped GitLab job the binutils whose objdump PyInstaller needs."""
    bootstrapped: dict[str, list[str]] = {}
    for name, raw in _mapping(_load_yaml(ROOT / ".gitlab-ci.yml")).items():
        if isinstance(raw, dict):
            script = _strings(_mapping(raw).get("before_script", []))
            if "uv tree --frozen --offline --universal --depth 0" in script:
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
        "--no-python-downloads python -m tools.quality.governance"
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


ETHOS = "github:HengYangDS/ethos"
ETHOS_RUN = "mise exec --locked -- ethos "
ETHOS_LINUX_PACKAGES = {"git", "libatomic1", "openssh-client"}


def test_ethos_release_is_pinned_once_and_locked_with_its_attestations() -> None:
    """One exact ETHOS release is pinned in ETHOS's own form and locked with attested archives."""
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())
    lock = tomllib.loads((ROOT / ".config/mise/mise.lock").read_text())
    version = toolchain["tools"][ETHOS]
    assert isinstance(version, str)
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version)
    assert not [name for name in toolchain["tools"] if name.endswith(":ethos")]
    assert toolchain["settings"]["locked_verify_provenance"] is True
    (locked,) = lock["tools"][ETHOS]
    assert locked["version"] == version
    platforms = {
        name.removeprefix("platforms."): entry
        for name, entry in locked.items()
        if name.startswith("platforms.")
    }
    assert set(platforms) == set(toolchain["settings"]["lockfile_platforms"])
    for entry in platforms.values():
        assert f"/releases/download/v{version}/ethos-{version}-" in entry["url"]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry["checksum"])
        assert entry["provenance"] == "github-attestations"


def test_commit_admission_runs_the_locked_ethos_release_on_both_peers() -> None:
    """Both peers install only the pinned ETHOS through Mise, then bind trust and admit with it."""
    github = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    job = _mapping(github["commit-admission"])
    environment = _mapping(job["env"])
    assert environment == {
        "ETHOS_COMMIT_ALLOWED_SIGNERS": "${{ vars.ETHOS_COMMIT_ALLOWED_SIGNERS }}",
        "MISE_ENABLE_TOOLS": ETHOS,
    }
    steps = tuple(_mapping(step) for step in _sequence(job["steps"]))
    uses = [_string(step.get("uses", "")).split("@", 1)[0] for step in steps]
    assert "astral-sh/setup-uv" not in uses
    mise = steps[uses.index("jdx/mise-action")]
    options = _mapping(mise["with"])
    assert options["install"] == options["cache"] == "true"
    assert options["install_args"] == ETHOS
    commands = [_string(step["run"]) for step in steps if "run" in step]
    preflight, refresh, install, bind, admit = commands
    assert preflight.startswith("test ")
    assert "ETHOS_COMMIT_ALLOWED_SIGNERS" in preflight
    assert refresh == "sudo apt-get update -qq"
    assert install.startswith("sudo apt-get install -qq -y --no-install-recommends ")
    assert set(install.split()) >= ETHOS_LINUX_PACKAGES
    assert steps.index(mise) < next(i for i, step in enumerate(steps) if step.get("run") == refresh)
    assert bind == f"{ETHOS_RUN}hook bind-trust --apply --root . --json"
    assert admit.startswith(f"{ETHOS_RUN}hook commit-range ")
    assert "ethos-source" not in yaml.safe_dump(job)

    gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
    source_image = _mapping(_mapping(gitlab["source-and-governance"])["image"])
    for name in ("commit-admission", "commit-admission-accepted"):
        admission = _mapping(gitlab[name])
        assert _mapping(admission["image"]) == source_image
        variables = _mapping(admission["variables"])
        assert variables["MISE_ENABLE_TOOLS"] == ETHOS
        assert "PYTHONPATH" not in variables
        assert _strings(admission["before_script"]) == [
            preflight,
            "mise install --locked",
            refresh.removeprefix("sudo "),
            install.removeprefix("sudo "),
            bind,
        ]
        assert _strings(admission["script"]) == [admit]


def test_only_commit_admission_installs_ethos() -> None:
    """Every other Mise job enables a declared tool set without ETHOS, so none downloads it."""
    tools = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())["tools"].keys()
    enabled: dict[str, str] = {}
    for workflow in (".github/workflows/verify.yml", ".github/workflows/admission.yml"):
        for name, raw in _mapping(_load_yaml(ROOT / workflow)["jobs"]).items():
            job = _mapping(raw)
            steps = tuple(_mapping(step) for step in _sequence(job.get("steps", [])))
            if any(_string(step.get("uses", "")).startswith(_MISE_ACTION) for step in steps):
                enabled[name] = _string(_mapping(job.get("env", {})).get("MISE_ENABLE_TOOLS", ""))
    for name, raw in _mapping(_load_yaml(ROOT / ".gitlab-ci.yml")).items():
        if isinstance(raw, dict) and "mise install --locked" in raw.get("before_script", []):
            variables = _mapping(_mapping(raw).get("variables", {}))
            enabled[f"gitlab:{name}"] = _string(variables.get("MISE_ENABLE_TOOLS", ""))
    assert len(enabled) >= 6
    for name, selection in enabled.items():
        selected = set(selection.split(","))
        assert selected <= tools, name
        admission = name.removeprefix("gitlab:").startswith("commit-admission")
        assert (ETHOS in selected) == admission, name


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
