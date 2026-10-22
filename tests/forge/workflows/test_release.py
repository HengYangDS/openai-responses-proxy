"""Native artifact construction, publication and installed-journey wiring."""

import re
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path

from tests.forge.workflows.fixtures import _load_yaml
from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _string
from tests.forge.workflows.fixtures import _strings
from tests.quality.fixtures import ROOT
from tests.quality.fixtures import git
from tools.git_environment import isolated_config_environment
from tools.quality.python import source_manifest

GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


def test_native_asset_jobs_install_the_product_before_loading_noxfile() -> None:
    """Make the source package importable while Nox loads its release sessions."""
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    expected = {
        "native-assets": "uv sync --locked --group quality",
        "native-linux": ("uv sync --locked --group quality --python python --no-python-downloads"),
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
        if job_id == "native-linux":
            assert install["working-directory"] == "/workspace"

    native_steps = _sequence(_mapping(jobs["native-assets"])["steps"])
    native_build = next(
        _mapping(step)
        for step in native_steps
        if _mapping(step).get("name") == "Build and accept the native release asset"
    )
    command = shlex.split(_string(native_build["run"]))
    assert command[-1] == "--basetemp=${{ runner.temp }}/native-pytest"

    linux_steps = _sequence(_mapping(jobs["native-linux"])["steps"])
    linux_build = next(
        _mapping(step)
        for step in linux_steps
        if _mapping(step).get("name") == "Build the native release asset"
    )
    assert linux_build["working-directory"] == "/workspace"
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
        and _mapping(step).get("run") == ("uv run --locked --no-sync nox -s native_lifecycle")
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
    assert compatibility["needs"] == ["python-matrix", "native-assets", "native-linux"]
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
    candidate = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/download-artifact@")
    )
    assert _mapping(candidate["with"]) == {
        "name": "native-${{ matrix.platform }}",
        "path": "${{ runner.temp }}/candidate-assets",
    }
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
        '--repository "${{ github.repository }}" --candidate-source . '
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
    assert _mapping(proof["env"])["OPENAI_RESPONSES_PROXY_CURRENT_RELEASE_ASSETS"] == (
        "${{ runner.temp }}/candidate-assets"
    )


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


def test_native_release_runtime_is_exact_and_platform_independent() -> None:
    native_runtime = (ROOT / ".config/mise/python-release").read_text(encoding="ascii").strip()
    image = (ROOT / ".config/ci/linux-release-image").read_text(encoding="ascii").strip()
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
        "commit-admission",
        "python-matrix",
        "tag-metadata",
        "native-assets",
        "native-linux",
        "native-linux-lifecycle",
    }
    assert verify.count("uv run --locked --no-sync python -m tools.release.artifact assemble") == 1
    assert verify.count("--sign") == 1
    assert "container: ${{ needs.python-matrix.outputs.linux-release-image }}" in verify
