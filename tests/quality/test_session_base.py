"""A test session keeps its temporary base while another session of the same user ends.

The repository removes every test's temporary directory when the test ends. Under that policy
pytest does not lock a session's numbered base directory, and each session that ends removes
the unlocked ones that it finds in the user's shared root: a session beside another one lost
its base in the middle of its run. The shared configuration therefore gives each session a
base of its own, inside the directory that the session already owns for its logs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_SECONDS = 30.0
# The first session takes its base, says so, and holds until the test lets it go.
_HOLDING = f"""
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_holds_its_base(tmp_path_factory, own_logs):
    base = tmp_path_factory.getbasetemp()
    assert base.is_relative_to(own_logs.session)
    (HERE / "ready").write_text(str(base), encoding="utf-8")
    deadline = time.monotonic() + {_SECONDS}
    while not (HERE / "go").exists():
        assert time.monotonic() < deadline, "the test never let this session go"
        time.sleep(0.05)
    assert base.is_dir(), "another session's end removed this session's base"
"""
_ENDING = """
def test_uses_a_temporary_directory(tmp_path):
    assert tmp_path.is_dir()
"""


def _session(root: Path, body: str, pytestconfig: pytest.Config) -> Path:
    """Write one test tree under a copy of the shared configuration and the repository's policy."""
    root.mkdir()
    shared = Path(__file__).resolve().parents[1] / "conftest.py"
    (root / "conftest.py").write_bytes(shared.read_bytes())
    (root / "pytest.ini").write_text(
        "[pytest]\n"
        f"tmp_path_retention_policy = {pytestconfig.getini('tmp_path_retention_policy')}\n"
        f"tmp_path_retention_count = {pytestconfig.getini('tmp_path_retention_count')}\n",
        encoding="utf-8",
    )
    (root / "test_session.py").write_text(body, encoding="utf-8")
    return root


def test_this_session_has_no_numbered_base_in_the_shared_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Its own base, or the one that its caller named: never `pytest-of-<user>/pytest-N`."""
    assert not tmp_path_factory.getbasetemp().parent.name.startswith("pytest-of-")


def test_a_session_keeps_its_base_while_another_session_of_the_user_ends(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    holding = _session(tmp_path / "holding", _HOLDING, pytestconfig)
    ending = _session(tmp_path / "ending", _ENDING, pytestconfig)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    environment = {**os.environ, "TMPDIR": str(temporary)}
    command = [sys.executable, "-m", "pytest", "-q", "--rootdir", "."]

    first = subprocess.Popen(
        command,
        cwd=holding,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + _SECONDS
        while not (holding / "ready").exists():
            assert first.poll() is None, first.communicate()[0]
            assert time.monotonic() < deadline, "the first session never took its base"
            time.sleep(0.05)
        second = subprocess.run(
            command,
            cwd=ending,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_SECONDS,
            check=False,
        )
        (holding / "go").write_text("", encoding="utf-8")
        output, _ = first.communicate(timeout=_SECONDS)
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()

    assert second.returncode == 0, second.stdout + second.stderr
    assert first.returncode == 0, output
    # Each session took its directory with it, the base included.
    assert list(temporary.iterdir()) == []
