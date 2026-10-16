"""Complete native workflow-checker supply and integration contracts."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.ci.project import render


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
