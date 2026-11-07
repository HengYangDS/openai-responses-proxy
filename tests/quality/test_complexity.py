"""Native Ruff admission of complexity and public-interface policy by repository role."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib

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
    path: str, *, excess: bool
) -> None:
    config = ROOT / ".config/checks/ruff/ruff.toml"
    policy = tomllib.loads(config.read_text(encoding="utf-8"))
    limit = policy["lint"]["mccabe"]["max-complexity"]
    source = "\n".join(
        [
            '"""Isolated native complexity input."""',
            "def choose(value: str) -> int:",
            '    """Select one bounded test outcome."""',
            *[
                f'    if value == "{index}":\n        return {index}'
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
        cwd=ROOT,
        timeout=30,
    )
    assert result.stderr == ""
    diagnostics = json.loads(result.stdout)
    assert result.returncode == int(excess)
    assert [finding["code"] for finding in diagnostics] == (["C901"] if excess else [])


@pytest.mark.parametrize(
    ("relative", "source", "expected"),
    [
        (
            "src/openai_responses_proxy/probe.py",
            "def answer():\n    return 42\n",
            {"D100", "D103", "ANN201"},
        ),
        ("tools/probe.py", "def answer():\n    return 42\n", {"D100", "D103", "ANN201"}),
        ("noxfile.py", "def answer():\n    return 42\n", {"D100", "D103", "ANN201"}),
        (
            "src/openai_responses_proxy/probe.py",
            (
                '"""Native annotation probe."""\ndef answer() -> int:\n'
                '    """Return the answer."""\n    return 42\n'
            ),
            set(),
        ),
        (
            "tools/probe.py",
            (
                '"""Native annotation probe."""\nclass Input:\n    """One public input."""\n'
                "    def __init__(self, value: int):\n"
                '        """Bind the selected value."""\n        self.value = value\n'
            ),
            {"ANN204"},
        ),
        ("tests/probe.py", "def answer():\n    return 42\n", {"D100"}),
        (
            "tests/probe.py",
            'def answer():\n    """Return the answer"""\n    return 42\n',
            {"D100", "D415"},
        ),
        (
            "tools/probe.py",
            (
                '"""Public tool contract."""\ndef read(path: str, mode: str) -> str:\n'
                '    """Read a selected record.\n\n'
                '    Args:\n        path: Record path.\n    """\n    return path + mode\n'
            ),
            {"D417"},
        ),
        (
            "src/openai_responses_proxy/probe.py",
            (
                '"""Public product contract."""\ndef read(path: str, mode: str) -> str:\n'
                '    """Read a selected record.\n\n'
                "    Args:\n        path: Record path.\n        mode: Read mode.\n"
                '    """\n    return path + mode\n'
            ),
            set(),
        ),
    ],
)
def test_native_ruff_applies_public_interface_policy_by_semantic_role(
    relative: str, source: str, expected: set[str]
) -> None:
    result = subprocess.run(
        (
            "ruff",
            "check",
            "--config",
            str(ROOT / ".config/checks/ruff/ruff.toml"),
            "--output-format",
            "json",
            "--stdin-filename",
            str(ROOT / relative),
            "-",
        ),
        input=source,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    assert result.stderr == ""
    assert result.returncode == bool(expected), result.stderr
    assert {item["code"] for item in json.loads(result.stdout)} == expected
