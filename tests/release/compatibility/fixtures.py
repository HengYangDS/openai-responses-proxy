"""Authentic predecessor qualification and native transition observations."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control as lifecycle_control
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release.fixtures import ROOT
from tests.release.fixtures import legacy_published_bundle
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import post_response
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import signing_inputs
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tests.serving.handoff.fixtures import wait_until
from tools.release.verification import cleanup_runtime


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable)
    if value is None:
        pytest.skip(f"{variable} is supplied by the release compatibility session")
    return Path(value).resolve(strict=True)


def _version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    assert len(parts) == 3
    assert all(part.isascii() and part.isdigit() for part in parts)
    major, minor, patch = parts
    return int(major), int(minor), int(patch)


def _assert_same_runtime_identity(
    command_result: Mapping[str, object],
    observed_status: Mapping[str, object],
) -> None:
    """Prove two observations identify one process serving one payload."""
    command_runtime = command_result.get("runtime")
    observed_runtime = observed_status.get("runtime")
    assert isinstance(command_runtime, dict), command_result
    assert isinstance(observed_runtime, dict), observed_status
    identity_fields = (
        "pid",
        "release",
        "serving_payload_sha256",
        "release_receipt_sha256",
        "payload_manifest_sha256",
    )
    assert {field: command_runtime.get(field) for field in identity_fields} == {
        field: observed_runtime.get(field) for field in identity_fields
    }
    assert observed_runtime.get("accepting") is True


def _wait_for_upgrade_drain(
    future: Future[dict[str, object]],
    ctx: runtime_context.RuntimeContext,
    *,
    predecessor_pid: int,
    predecessor_release: str,
    timeout_seconds: float,
) -> bool:
    """Release held requests after materialization reaches native drain."""

    def draining_or_finished() -> bool:
        runtime = lifecycle_control.read_runtime(ctx)
        return (
            isinstance(runtime, dict)
            and runtime.get("pid") == predecessor_pid
            and runtime.get("release") == predecessor_release
            and runtime.get("accepting") is False
            and runtime.get("draining") is True
        ) or future.done()

    reached = wait_until(draining_or_finished, timeout_seconds)
    if future.done():
        future.result()
    return reached


def _materialize_native_bundle(candidate: artifact.VerifiedArtifact, output: Path) -> Path:
    """Materialize exact admitted native executable bytes without its provider manifest."""
    output.mkdir()
    for blob in candidate.peek_blobs():
        if blob.path == "providers.toml":
            continue
        relative = PurePosixPath(blob.path)
        assert relative.parts[0] == "bin"
        target = output.joinpath(*relative.parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.content)
        target.chmod(0o755 if blob.mode == "100755" else 0o644)
    platform_id = product_identity.native_release_platform(platform.system(), platform.machine())
    executable = output / product_identity.executable_name(
        windows=platform_id.startswith("windows-")
    )
    assert executable.is_file()
    return output


def admitted_predecessor(
    tmp_path: Path,
    *,
    previous_asset: Path,
    previous_trust: Path,
    current_version: str,
) -> tuple[artifact.VerifiedArtifact, Path, str, Path]:
    """Qualify authentic published installation before the retained-state transition."""
    published_predecessor = artifact.admit(
        previous_asset,
        trust_anchor=previous_trust,
    )
    previous_version = published_predecessor.version
    assert _version(previous_version) < _version(current_version)
    previous_bundle = _materialize_native_bundle(
        published_predecessor,
        tmp_path / "published-predecessor-bundle",
    )
    previous_executable = previous_bundle / product_identity.executable_name(
        windows=platform.system() == "Windows"
    )

    published_home = tmp_path / "published-home"
    published_install = tmp_path / "published-payload"
    published_state = tmp_path / "published-state"
    published_home.mkdir()
    published_port = free_port()
    published_context = runtime_context_for(
        published_home,
        published_install,
        published_state,
        published_port,
    )
    published_environment = native_process_environment(
        user_home=published_home,
        install_root=published_install,
        state_root=published_state,
    )
    with ExitStack() as published_cleanups:
        published_cleanups.callback(cleanup_runtime, published_context)
        installed_published = run_command(
            previous_executable,
            published_environment,
            "install",
            "--asset",
            str(previous_asset),
            "--trust-anchor",
            str(previous_trust),
            "--port",
            str(published_port),
            "--json",
        )
        assert installed_published["state"] == "installed"
        published_status = run_command(
            previous_executable,
            published_environment,
            "status",
            "--port",
            str(published_port),
            "--json",
        )
        assert published_status["release"] == previous_version
        assert (
            run_command(
                previous_executable,
                published_environment,
                "doctor",
                "--port",
                str(published_port),
                "--json",
            )["ok"]
            is True
        )
        run_command(
            previous_executable,
            published_environment,
            "uninstall",
            "--port",
            str(published_port),
            "--purge",
            "--json",
        )
        assert not published_install.exists()
        assert not records.transaction_root(published_context).exists()
    return published_predecessor, previous_bundle, previous_version, previous_executable


def renamed_product_lifecycle(
    tmp_path: Path,
    *,
    current_executable: Path,
    current_bundle: Path,
    previous_asset: Path,
    previous_trust: Path,
) -> None:
    """Cross the public identity change and its native reverse path."""
    previous_bundle, previous_version = legacy_published_bundle(
        previous_asset, previous_trust, tmp_path
    )
    old_name = "codex-responses-proxy" + (".exe" if os.name == "nt" else "")
    previous_executable = previous_bundle / old_name
    home = tmp_path / "home"
    home.mkdir()
    install = tmp_path / "old-payload"
    state = tmp_path / "old-state"
    successor_install = tmp_path / "payload"
    successor_state = tmp_path / "state"
    port = free_port()
    environment = native_process_environment(
        user_home=home, install_root=successor_install, state_root=successor_state
    )
    old_environment = {
        name: value for name, value in environment.items() if not name.startswith("OPENAI_")
    }
    old_environment.update(
        CODEX_RESPONSES_PROXY_HOME=str(install),
        CODEX_RESPONSES_PROXY_STATE_HOME=str(state),
    )
    ctx = runtime_context_for(home, successor_install, successor_state, port)
    canonical = process.listener_pids(runtime_config.DEFAULT_PORT)
    original_projection = native_service_projection(runtime_context.create())
    key, trust, anchor = signing_inputs(tmp_path)
    upstream = ScriptedUpstream()
    upstream.start()
    asset = signed_asset(
        current_bundle,
        tmp_path / "candidate-assets",
        version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
        upstream_url=upstream.base_url(),
        key=key,
        trust=trust,
        preserve_manifest=True,
    )
    old_present = False
    try:
        for cycle in range(2):
            previous = run_command(
                previous_executable,
                old_environment,
                "install",
                "--asset",
                str(previous_asset),
                "--trust-anchor",
                str(previous_trust),
                "--port",
                str(port),
                "--json",
            )
            old_present = True
            assert previous["state"] == "installed"
            status = run_command(
                previous_executable, old_environment, "status", "--port", str(port), "--json"
            )
            assert status["state"] == "running"
            assert status["release"] == previous_version
            assert status["payload_transaction"] is None
            removed = run_command(
                previous_executable,
                old_environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
            )
            assert removed["state"] == "purged"
            old_present = False
            assert not install.exists()
            assert process.listener_pids(port) == []
            if cycle == 1:
                break
            installed = run_command(
                current_executable,
                environment,
                "install",
                "--asset",
                str(asset),
                "--trust-anchor",
                str(anchor),
                "--upstream-base-url",
                upstream.base_url(),
                "--port",
                str(port),
                "--json",
            )
            assert installed["state"] == "installed"
            upstream.push((200, b'{"id":"renamed","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"renamed","status":"completed"}'
            )
            assert (
                run_command(
                    current_executable, environment, "reload", "--port", str(port), "--json"
                )["state"]
                == "reloaded"
            )
            assert run_command(
                current_executable, environment, "recover", "--port", str(port), "--json"
            ) == {"state": "not_required", "next": None}
            removed = run_command(
                current_executable,
                environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
            )
            assert removed["state"] == "purged"
            assert not successor_install.exists()
            assert native_service_projection(ctx)["status"] == "absent"
            assert process.listener_pids(port) == []
    finally:
        if old_present:
            run_command(
                previous_executable,
                old_environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
            )
        cleanup_runtime(ctx)
        upstream.close()
    assert process.listener_pids(runtime_config.DEFAULT_PORT) == canonical
    assert native_service_projection(runtime_context.create()) == original_projection
