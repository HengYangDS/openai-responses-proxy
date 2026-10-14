"""Native complexity admission across every repository Python role."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT


@pytest.mark.parametrize(
    "path",
    [
        "src/openai_responses_proxy/relay/probe.py",
        "tools/probe.py",
        "tests/test_probe.py",
        "noxfile.py",
    ],
)
@pytest.mark.parametrize("excess", [False, True])
def test_complexity_threshold_uses_native_admission_for_every_role(
    path: str, excess: bool, tmp_path: Path
) -> None:
    config = ROOT / ".config/checks/ruff/ruff.toml"
    policy = tomllib.loads(config.read_text(encoding="utf-8"))
    limit = policy["lint"]["mccabe"]["max-complexity"]
    source = "\n".join(
        [
            '"""Isolated native complexity input."""',
            "def choose(value: int) -> int:",
            '    """Select one bounded test outcome."""',
            *[
                f"    if value == {index}:\n        return {index}"
                for index in range(limit - 1 + excess)
            ],
            "    return -1",
            "",
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--config",
            str(config),
            "--output-format=json",
            "--stdin-filename",
            path,
            "-",
        ],
        input=source,
        text=True,
        capture_output=True,
        check=False,
        cwd=tmp_path,
        timeout=30,
    )
    assert result.stderr == ""
    diagnostics = json.loads(result.stdout)
    assert result.returncode == int(excess)
    assert [finding["code"] for finding in diagnostics] == (["C901"] if excess else [])
