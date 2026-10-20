"""Complete native workflow-checker supply and integration contracts."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.quality.fixtures import ROOT
from tools.ci.project import render


@pytest.mark.repository_toolchain
@pytest.mark.parametrize("injection", ["explicit", "user", "disabled"])
def test_native_developer_uv_uses_only_the_repository_configuration(
    tmp_path: Path, injection: str
) -> None:
    user = tmp_path / "uv"
    user.mkdir()
    injected = user / "uv.toml"
    injected.write_text('required-version = "==99.0.0"\n')
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SystemRoot", "COMSPEC"}
    }
    if injection == "explicit":
        environment["UV_CONFIG_FILE"] = str(injected)
    elif injection == "user":
        environment.update(XDG_CONFIG_HOME=str(tmp_path), APPDATA=str(tmp_path))
    else:
        environment["UV_NO_CONFIG"] = "1"
    before = injected.read_bytes()
    executable = shutil.which("mise")
    assert executable is not None
    command = (executable, "exec", "--locked", "--", "uv")
    result = subprocess.run(
        (*command, "tree", "--frozen", "--offline", "--universal", "--depth", "0"),
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "openai-responses-proxy" in result.stdout
    binding = subprocess.run(
        (*command[:-1], "python", "-c", "import os; print(os.environ['UV_CONFIG_FILE'])"),
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert Path(binding.stdout.strip()) == ROOT / ".config/mise/uv.toml"
    assert injected.read_bytes() == before


@pytest.mark.repository_toolchain
def test_peer_uv_consumers_keep_project_and_external_configuration_separate() -> None:
    github = yaml.safe_load(render("githubVerify"))
    gitlab = yaml.safe_load(render("gitlab"))
    expected = {"UV_CONFIG_FILE": ".config/mise/uv.toml", "UV_NO_CONFIG": "false"}
    for name, job in github["jobs"].items():
        environment = job.get("env", {})
        if name == "commit-admission":
            assert expected.keys().isdisjoint(environment)
        else:
            assert environment.items() >= expected.items()
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                assert step["with"]["version-file"] == expected["UV_CONFIG_FILE"]
    for name, job in gitlab.items():
        if not isinstance(job, dict) or "script" not in job:
            continue
        environment = job.get("variables", {})
        if name.startswith("commit-admission"):
            assert expected.keys().isdisjoint(environment)
        else:
            assert environment.items() >= expected.items()


def test_workflow_tasks_require_every_locked_native_integration() -> None:
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())
    quality = tomllib.loads((ROOT / "pyproject.toml").read_text())["dependency-groups"]["quality"]
    assert "aqua:koalaman/shellcheck" in toolchain["tools"]
    assert any(requirement.startswith("pyflakes==") for requirement in quality)
    assert toolchain["tasks"]["workflow-supply"]["run"] == [
        "actionlint -version",
        "shellcheck --version",
        'uv run --locked --no-sync --python "{{tools.python.path}}" pyflakes --version',
    ]
    assert toolchain["tasks"]["workflow-check"]["depends"] == ["workflow-supply"]
    assert toolchain["tasks"]["workflow-check"]["run"] == (
        'uv run --locked --no-sync --python "{{tools.python.path}}" actionlint'
    )


@pytest.mark.repository_toolchain
def test_selected_peer_tools_include_every_native_workflow_executable() -> None:
    toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text())
    selected = set(render("#Toolchains.quality", "text").decode().strip().split(","))
    assert {"github:rhysd/actionlint", "aqua:koalaman/shellcheck"} <= selected
    assert selected <= toolchain["tools"].keys()


@pytest.mark.repository_toolchain
@pytest.mark.parametrize(
    ("shell", "script", "diagnostic"),
    [
        ("python", 'print("ready")', None),
        ("python", "print(undefined_native_probe)", "pyflakes"),
        ("bash", 'value="a b"\necho $value', "shellcheck"),
    ],
)
def test_native_workflow_task_keeps_both_original_integrations(
    tmp_path: Path, shell: str, script: str, diagnostic: str | None
) -> None:
    workflow = tmp_path / "native-checker.yml"
    indented = "\n".join(f"          {line}" for line in script.splitlines())
    workflow.write_text(
        "name: Native checker acceptance\non: push\njobs:\n"
        "  proof:\n    runs-on: ubuntu-24.04\n    steps:\n"
        f"      - name: Probe\n        shell: {shell}\n        run: |\n{indented}\n"
    )
    original = workflow.read_bytes()
    result = subprocess.run(
        ("mise", "run", "--timeout", "20s", "workflow-check", str(workflow)),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert workflow.read_bytes() == original
    if diagnostic is None:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert f"[{diagnostic}]" in result.stdout + result.stderr
