"""A test logs only below the run's temporary root, where the session's default logs lie."""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

import pytest

from openai_responses_proxy.lifecycle.supervision import watchdog
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.runtime import bounded_log
from openai_responses_proxy.runtime import config as runtime_config

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

# A test file that a child pytest runs under a copy of the shared configuration. The child's
# temporary directory and its --basetemp are two folders beside this file, so the folder
# "outside" lies outside the child's temporary root and inside the temporary root of this run.
_CHILD = """
import contextlib
import importlib
import tempfile
from pathlib import Path

from openai_responses_proxy.lifecycle.supervision import watchdog
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.runtime import bounded_log

OUTSIDE = Path(__file__).resolve().parent / "outside" / "proxy.log"


def log(path):
    bounded_log.append(path, "event=probe", max_bytes=4096, backup_count=0)


def test_swallows_the_refusal():
    with contextlib.suppress(OSError):
        log(OUTSIDE)


def test_logs_below_the_temporary_directory():
    with tempfile.TemporaryDirectory() as directory:
        log(Path(directory) / "proxy.log")
        assert (Path(directory) / "proxy.log").is_file()


def test_logs_below_the_basetemp(tmp_path):
    log(tmp_path / "proxy.log")
    assert (tmp_path / "proxy.log").is_file()


def test_the_default_logs_are_not_the_ones_that_the_host_named(own_logs):
    assert Path(operational_log.LOG_PATH).parent.parent == own_logs.session
    assert Path(importlib.reload(watchdog).LOG_PATH).parent.parent == own_logs.session
"""
_CHILD_SECONDS = 60.0
_MAX_BYTES = 4096


class _OwnLogs(Protocol):
    """The guard that the shared test configuration hands to a test."""

    session: Path
    roots: tuple[Path, ...]
    refused: list[Path]


def _refusal(path: Path) -> str:
    return re.escape(
        f"log line to {path} refused: the path lies outside the temporary root of this run, "
        "and a test keeps its log below that root"
    )


@pytest.fixture
def outside(tmp_path: Path, own_logs: _OwnLogs, mocker: MockerFixture) -> Path:
    """Return a log path beside the one folder to which this test narrows the run's roots.

    Both folders lie in the test's own temporary directory. The test therefore holds wherever
    the checkout lies, a copy below the temporary directory included, and a guard that failed
    would write there and not to a log of the host. The run's own roots are held by the test of
    an admitted line and by the child run, whose roots are the two folders that it names.
    """
    inside = tmp_path / "inside"
    inside.mkdir()
    mocker.patch.object(own_logs, "roots", (inside.resolve(),))
    directory = tmp_path / "outside"
    directory.mkdir()
    return directory / "outside.log"


def _append(path: Path, _mocker: MockerFixture) -> None:
    bounded_log.append(path, "event=refused", max_bytes=_MAX_BYTES, backup_count=0)


def _proxy(path: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(operational_log, "LOG_PATH", str(path))
    operational_log.log("event=refused")


def _watchdog(path: Path, mocker: MockerFixture) -> None:
    fresh = importlib.reload(watchdog)
    mocker.patch.object(fresh, "LOG_PATH", str(path))
    fresh._log("event=refused")


@pytest.mark.parametrize("write", [_append, _proxy, _watchdog])
def test_a_log_line_outside_the_temporary_root_is_refused_and_writes_no_file(
    write: Callable[[Path, MockerFixture], None],
    outside: Path,
    own_logs: _OwnLogs,
    *,
    mocker: MockerFixture,
) -> None:
    with pytest.raises(PermissionError, match=_refusal(outside)) as refusal:
        write(outside, mocker)

    assert type(refusal.value) is not PermissionError
    assert list(outside.parent.iterdir()) == []
    assert own_logs.refused == [outside]
    # This test met its refusal on purpose; left in place it would fail the test when it ends.
    own_logs.refused.clear()


def test_a_link_below_the_temporary_root_that_leads_outside_is_refused(
    tmp_path: Path, outside: Path, own_logs: _OwnLogs
) -> None:
    """The guard reads where a path leads, not how it is spelled."""
    link = tmp_path / "inside" / "state"
    link.symlink_to(outside.parent, target_is_directory=True)
    path = link / outside.name

    with pytest.raises(PermissionError, match=_refusal(path)):
        bounded_log.append(path, "event=refused", max_bytes=_MAX_BYTES, backup_count=0)

    assert list(outside.parent.iterdir()) == []
    assert own_logs.refused == [path]
    own_logs.refused.clear()


def test_a_log_line_below_the_temporary_root_is_written(tmp_path: Path, own_logs: _OwnLogs) -> None:
    with tempfile.TemporaryDirectory() as directory:
        for path in (tmp_path / "proxy.log", Path(directory) / "proxy.log"):
            bounded_log.append(path, "event=admitted", max_bytes=_MAX_BYTES, backup_count=0)
            assert path.read_text(encoding="utf-8").endswith(" event=admitted\n")

    assert own_logs.refused == []


def test_the_default_logs_lie_in_the_directory_of_the_session(own_logs: _OwnLogs) -> None:
    fresh = importlib.reload(watchdog)
    state = Path(runtime_config.state_dir())

    assert state.parent == own_logs.session
    assert Path(operational_log.LOG_PATH) == state / "proxy.log"
    assert Path(fresh.LOG_PATH) == state / "watchdog.log"
    assert own_logs.session.is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_a_test_whose_refusal_is_swallowed_fails_when_it_ends(tmp_path: Path) -> None:
    shared = Path(__file__).resolve().parents[1] / "conftest.py"
    (tmp_path / "conftest.py").write_bytes(shared.read_bytes())
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_logs.py").write_text(_CHILD, encoding="utf-8")
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    named = tmp_path / "named"
    environment = {
        **os.environ,
        **dict.fromkeys(("TMPDIR", "TEMP", "TMP"), str(temporary)),
        runtime_config.STATE_HOME_ENV: str(named),
        runtime_config.PROXY_LOG_ENV: str(named / "proxy.log"),
        runtime_config.WATCHDOG_LOG_ENV: str(named / "watchdog.log"),
    }

    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--rootdir", ".", "--basetemp", "basetemp"],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_CHILD_SECONDS,
        check=False,
    )

    # Every test passes its own body; the fixture fails the one whose refusal it found.
    assert child.returncode == 1, child.stdout + child.stderr
    assert "4 passed, 1 error" in child.stdout
    assert "ERROR at teardown of test_swallows_the_refusal" in child.stdout
    assert re.search(_refusal(tmp_path / "outside" / "proxy.log"), child.stdout)
    # Nothing was written outside the child's root or to a log that its host named, and the
    # child's session took its directory with it.
    assert not (tmp_path / "outside").exists()
    assert not named.exists()
    assert list(temporary.iterdir()) == []
