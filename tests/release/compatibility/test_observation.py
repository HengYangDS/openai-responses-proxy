"""Native release context and materialization-to-drain observation contracts."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from pathlib import PureWindowsPath

import pytest

from openai_responses_proxy.lifecycle import control as lifecycle_control
from tests.release.compatibility.fixtures import _wait_for_upgrade_drain
from tests.release.fixtures import runtime_context_for

pytestmark = [
    pytest.mark.native_distribution,
]


def test_upgrade_drain_releases_held_requests(tmp_path: Path, *, mocker) -> None:
    """Release held requests once native replacement closes admission."""
    ctx = runtime_context_for(
        tmp_path / "home",
        tmp_path / "payload",
        tmp_path / "state",
        43210,
    )
    future: Future[dict[str, object]] = Future()
    mocker.patch.object(
        lifecycle_control,
        "read_runtime",
        return_value={
            "pid": 1234,
            "release": "3.1.2",
            "accepting": False,
            "draining": True,
        },
    )

    assert _wait_for_upgrade_drain(
        future,
        ctx,
        predecessor_pid=1234,
        predecessor_release="3.1.2",
        timeout_seconds=0.01,
    )


def test_upgrade_drain_waits_through_materialization(tmp_path: Path, *, mocker) -> None:
    """A materialized candidate is not yet at the native drain boundary."""
    ctx = runtime_context_for(
        tmp_path / "home",
        tmp_path / "payload",
        tmp_path / "state",
        43210,
    )
    future: Future[dict[str, object]] = Future()
    mocker.patch.object(
        lifecycle_control,
        "read_runtime",
        side_effect=[
            {
                "pid": 1234,
                "release": "3.1.2",
                "accepting": True,
                "draining": False,
            },
            {
                "pid": 1234,
                "release": "3.1.2",
                "accepting": False,
                "draining": True,
            },
        ],
    )
    mocker.patch(
        "tests.serving.handoff.fixtures.time.monotonic",
        side_effect=[0.0, 0.0, 0.2],
    )
    mocker.patch("tests.serving.handoff.fixtures.time.sleep")

    assert _wait_for_upgrade_drain(
        future,
        ctx,
        predecessor_pid=1234,
        predecessor_release="3.1.2",
        timeout_seconds=0.2,
    )


def test_runtime_context_uses_the_native_command_projection(tmp_path: Path, *, mocker) -> None:
    """Build release-test paths through the same owner as production."""
    mocker.patch("tests.release.fixtures.platform.system", return_value="Windows")
    home = tmp_path / "home"
    install = tmp_path / "payload"
    ctx = runtime_context_for(home, install, tmp_path / "state", 43210)

    assert PureWindowsPath(ctx.executable) == PureWindowsPath(
        install / "bin" / "openai-responses-proxy.exe"
    )
    assert PureWindowsPath(ctx.command) == PureWindowsPath(
        home / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "openai-responses-proxy.cmd"
    )
