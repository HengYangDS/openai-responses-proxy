"""A subprocess read in text mode names its encoding.

Without one, Python decodes a child's output with the locale's encoding, which is cp1252 on
Windows runners, so UTF-8 output from Ruff, ty or Git fails there. Ruff's PLW1514 covers only
open() and its relatives, not subprocess.
"""

from __future__ import annotations

import ast
import subprocess

import pytest

from tests.quality.fixtures import ROOT

# The subprocess calls that accept text=True or universal_newlines=True.
TEXT_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
TEXT_MODES = ("text", "universal_newlines")


def undecoded_text_calls(source: str, filename: str) -> list[str]:
    """Return each subprocess call that reads text without naming its encoding."""
    gaps = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else None
        if name not in TEXT_CALLS:
            continue
        keywords = {keyword.arg for keyword in node.keywords}
        text_mode = any(
            keyword.arg in TEXT_MODES
            and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
            for keyword in node.keywords
        )
        if text_mode and "encoding" not in keywords:
            gaps.append(f"{filename}:{node.lineno}")
    return gaps


def _tracked_python() -> list[str]:
    completed = subprocess.run(
        ("git", "ls-files", "-z", "--", "src/*.py", "tools/*.py", "tests/*.py", "noxfile.py"),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return [path for path in completed.stdout.decode().split("\0") if path]


def test_every_text_mode_subprocess_names_its_encoding() -> None:
    gaps = [
        gap
        for relative in _tracked_python()
        for gap in undecoded_text_calls((ROOT / relative).read_text(encoding="utf-8"), relative)
    ]
    assert gaps == []


@pytest.mark.parametrize(
    ("call", "allowed"),
    [
        ('subprocess.run(("ruff",), text=True, encoding="utf-8", check=True)', True),
        ('subprocess.run(("ruff",), text=True, check=True)', False),
        ('subprocess.check_output(("git",), universal_newlines=True)', False),
        ('subprocess.Popen(("git",), text=True, encoding="utf-8")', True),
        ('subprocess.run(("git",), capture_output=True, check=True)', True),
        ('subprocess.run(("git",), text=False, check=True)', True),
        ('subprocess.run(("git",), text=decoded, check=True)', False),
    ],
)
def test_a_text_mode_subprocess_needs_an_encoding(call: str, *, allowed: bool) -> None:
    gaps = undecoded_text_calls(f"import subprocess\n{call}\n", "probe.py")

    assert (gaps == []) is allowed
