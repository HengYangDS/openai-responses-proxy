"""The packaged lifecycle shares one native writer lock and read-only observation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from filelock import FileLock

from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.serving.handoff.fixtures import free_port

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


def test_native_lifecycle_rejects_competing_writers_without_blocking_readers(
    tmp_path: Path,
) -> None:
    """Every packaged mutator refuses the same lock before any installation effect."""
    executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(strict=True)
    home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
    home.mkdir()
    port = free_port()
    ctx = runtime_context_for(home, install, state, port)
    environment = native_process_environment(user_home=home, install_root=install, state_root=state)
    service_before = native_service_projection(ctx)
    output = ("--port", str(port), "--json")
    lock_path = state / "lifecycle.lock"

    with FileLock(lock_path, timeout=0, fallback_to_soft=False, preserve_lock_file=True):
        # Observe identity without reading the byte range that Windows exclusively locks.
        lock_metadata = lock_path.stat()
        for arguments in (
            ("install", "--asset", str(tmp_path / "absent.tar.gz"), "--trust-anchor", str(home)),
            ("recover",),
            ("reload",),
            ("rollback", "--to-release", "1.0.0"),
            ("uninstall", "--purge"),
        ):
            refused = run_command(executable, environment, *arguments, *output, expected=2)
            assert refused["error"] == {
                "code": "lifecycle_error",
                "message": "lifecycle mutation is already in progress",
                "next": "openai-responses-proxy doctor",
            }
        status = run_command(executable, environment, "status", *output)
        assert status["state"] == "not_installed"
        assert status["runtime"] is None
        assert status["payload_transaction"] is None
        doctor = run_command(executable, environment, "doctor", *output, expected=1)
        assert doctor["state"] == "not_installed"
        assert doctor["ok"] is False
        current_lock = lock_path.stat()
        assert (current_lock.st_dev, current_lock.st_ino) == (
            lock_metadata.st_dev,
            lock_metadata.st_ino,
        )
        assert current_lock.st_size == lock_metadata.st_size
        assert current_lock.st_mtime_ns == lock_metadata.st_mtime_ns
        assert current_lock.st_ctime_ns == lock_metadata.st_ctime_ns
        assert not install.exists()
        assert not Path(ctx.command).exists()
        assert not records.journal_path(ctx).exists()
        assert not records.transaction_root(ctx).exists()
        assert native_service_projection(ctx) == service_before

    assert run_command(executable, environment, "recover", *output) == {
        "state": "not_required",
        "next": None,
    }
    assert not install.exists()
    assert native_service_projection(ctx) == service_before
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "home",
        "state",
        "state/lifecycle.lock",
    ]
