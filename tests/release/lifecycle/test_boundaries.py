"""Native platform service-manager boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release import fixtures as release_fixtures
from tests.release.fixtures import ROOT
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import signing_inputs
from tests.serving.handoff.fixtures import free_port

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestSignedNativeLifecycle:
    @pytest.mark.skipif(sys.platform != "linux", reason="Linux user-service boundary")
    def test_unavailable_user_bus_leaves_no_installation(self, tmp_path: Path) -> None:
        executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(
            strict=True
        )
        home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
        home.mkdir()
        runtime_directory = home / "run"
        runtime_directory.mkdir(mode=0o700)
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home, install_root=install, state_root=state
        )
        environment.update(
            XDG_RUNTIME_DIR=str(runtime_directory),
            DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime_directory / 'bus'}",
        )
        observation = subprocess.run(
            ["systemctl", "--user", "show", "--property=SystemState", "--value"],
            env=environment,
            capture_output=True,
            check=False,
            timeout=release_fixtures.COMMAND_TIMEOUT_SECONDS,
        )
        assert observation.returncode != 0
        key, trust, anchor = signing_inputs(tmp_path)
        asset = signed_asset(
            executable.parent,
            tmp_path / "assets",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
            key=key,
            trust=trust,
        )

        result = run_command(
            executable,
            environment,
            "install",
            "--asset",
            str(asset),
            "--trust-anchor",
            str(anchor),
            "--port",
            str(port),
            "--json",
            expected=2,
        )
        assert result == {
            "error": {
                "code": "native_service_unavailable",
                "message": (
                    "a reachable systemd user manager is required for Linux installation; "
                    "enable a systemd user session and retry installation"
                ),
                "next": "openai-responses-proxy install --help",
            }
        }
        observed = run_command(executable, environment, "status", "--port", str(port), "--json")
        assert observed["payload_transaction"] is None
        assert observed["runtime"] is None
        assert observed["listener_pids"] == []
        assert observed["command"] == {"kind": None, "path": ctx.command, "state": "absent"}
        assert not install.exists()
        assert not records.journal_path(ctx).exists()
        assert not records.transaction_root(ctx).exists()
        assert not any(path.is_file() or path.is_symlink() for path in home.rglob("*"))
