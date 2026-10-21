"""Published predecessor upgrade, admission-preserving rollback and restoration."""

from __future__ import annotations

import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import control as lifecycle_control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.release import fixtures as native_release
from tests.release.compatibility.fixtures import _assert_same_runtime_identity
from tests.release.compatibility.fixtures import _required_path
from tests.release.compatibility.fixtures import _wait_for_upgrade_drain
from tests.release.compatibility.fixtures import admitted_predecessor
from tests.release.compatibility.fixtures import renamed_product_lifecycle
from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tests.serving.handoff.fixtures import wait_until
from tools.release import verification

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


def test_real_predecessor_upgrades_to_current_native_candidate(tmp_path: Path) -> None:
    current_executable = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
    current_bundle = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_BUNDLE")
    previous_asset = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET")
    previous_trust = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR")
    if previous_asset.name.startswith("codex-responses-proxy-"):
        renamed_product_lifecycle(
            tmp_path,
            current_executable=current_executable,
            current_bundle=current_bundle,
            previous_asset=previous_asset,
            previous_trust=previous_trust,
        )
        return

    current_version = (native_release.ROOT / "VERSION").read_text(encoding="ascii").strip()
    published_predecessor, previous_version, previous_executable = admitted_predecessor(
        tmp_path,
        previous_asset=previous_asset,
        previous_trust=previous_trust,
        current_version=current_version,
    )

    home, install, state = (
        tmp_path / "home",
        tmp_path / "payload",
        tmp_path / "state",
    )
    home.mkdir()
    port = free_port()
    ctx = native_release.runtime_context_for(home, install, state, port)
    environment = native_process_environment(
        user_home=home,
        install_root=install,
        state_root=state,
    )
    canonical_before = process.listener_pids(runtime_config.DEFAULT_PORT)

    def run_candidate(*arguments: str) -> dict[str, object]:
        return native_release.run_command(current_executable, environment, *arguments)

    command_output = ("--port", str(port), "--json")

    key, trust, anchor = native_release.signing_inputs(tmp_path)

    upstream = ScriptedUpstream()
    upstream.start()
    upstream_url = upstream.base_url().replace("127.0.0.1", "localhost")
    current_fixture = native_release.signed_asset(
        current_bundle,
        tmp_path / "current-assets",
        version=current_version,
        upstream_url=upstream_url,
        key=key,
        trust=trust,
    )
    previous_paths = {blob.path for blob in published_predecessor.peek_blobs()}
    current_paths = {
        blob.path for blob in artifact.admit(current_fixture, trust_anchor=anchor).peek_blobs()
    }

    with ExitStack() as cleanups:
        cleanups.callback(upstream.close)
        cleanups.callback(verification.cleanup_runtime, ctx)

        installed = native_release.run_command(
            previous_executable,
            environment,
            "install",
            "--asset",
            str(previous_asset),
            "--trust-anchor",
            str(previous_trust),
            "--upstream-base-url",
            upstream_url,
            *command_output,
        )
        assert installed["state"] == "installed"
        assert (
            native_release.run_command(
                previous_executable, environment, "doctor", "--port", str(port), "--json"
            )["ok"]
            is True
        )
        before = run_candidate(
            "status",
            *command_output,
        )
        assert before["release"] == previous_version
        before_runtime = before.get("runtime")
        assert isinstance(before_runtime, dict)
        assert before_runtime.get("release_receipt_sha256") == published_predecessor.receipt_sha256
        previous_pid = before_runtime.get("pid")
        assert type(previous_pid) is int
        upstream.push((200, b'{"id":"before","status":"completed"}'))
        assert (
            native_release.post_response(port, route="upstream")
            == b'{"id":"before","status":"completed"}'
        )

        stream_started = threading.Event()
        normal_started = threading.Barrier(4)
        release = threading.Event()

        def held_stream(handler) -> None:
            stream_started.set()
            release.wait(timeout=60)
            payload = (
                b'data: {"type":"response.output_text.delta","delta":"held"}\n\n'
                b'data: {"type":"response.completed"}\n\n'
            )
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        def held_response(handler) -> None:
            normal_started.wait(timeout=20)
            release.wait(timeout=60)
            payload = b'{"id":"held","status":"completed"}'
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        upstream.push(held_stream)
        held: dict[str, bytes] = {}
        stream_holder = threading.Thread(
            target=lambda: held.setdefault(
                "stream",
                native_release.post_response(port, stream=True, timeout=90, route="upstream"),
            ),
            daemon=True,
        )
        stream_holder.start()
        assert stream_started.wait(timeout=20)

        holders = []
        for index in range(3):
            upstream.push(held_response)
            holder = threading.Thread(
                target=lambda slot=index: held.setdefault(
                    f"normal-{slot}",
                    native_release.post_response(port, timeout=90, route="upstream"),
                ),
                daemon=True,
            )
            holders.append(holder)
            holder.start()
        normal_started.wait(timeout=20)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upgrade-command")
        cleanups.callback(executor.shutdown, wait=True, cancel_futures=True)
        cleanups.callback(release.set)

        upgrade_future = executor.submit(
            run_candidate,
            "install",
            "--asset",
            str(current_fixture),
            "--trust-anchor",
            str(anchor),
            "--port",
            str(port),
            "--timeout-seconds",
            "60",
            "--json",
        )
        assert _wait_for_upgrade_drain(
            upgrade_future,
            ctx,
            predecessor_pid=previous_pid,
            predecessor_release=previous_version,
            timeout_seconds=native_release.COMMAND_TIMEOUT_SECONDS,
        )
        release.set()
        stream_holder.join(timeout=60)
        for holder in holders:
            holder.join(timeout=60)
        upgraded = upgrade_future.result(timeout=90)
        assert not stream_holder.is_alive()
        assert all(not holder.is_alive() for holder in holders)
        assert held.get("stream") == (
            b'data: {"type":"response.output_text.delta","delta":"held"}\n\n'
            b'data: {"type":"response.completed"}\n\n'
        )
        assert {held.get(f"normal-{index}") for index in range(3)} == {
            b'{"id":"held","status":"completed"}'
        }
        assert upgraded["state"] == "upgraded"

        after = run_candidate(
            "status",
            *command_output,
        )
        assert after["release"] == current_version
        assert after["payload_transaction"] is None
        installed_command = Path(ctx.command)

        def run_installed(*arguments: str) -> dict[str, object]:
            return native_release.run_command(installed_command, environment, *arguments)

        control_executable = Path(generation.control_context(ctx).executable)
        assert command.status(installed_command, control_executable)["state"] == "owned"
        after_runtime = after.get("runtime")
        assert isinstance(after_runtime, dict), after
        candidate_pid = after_runtime.get("pid")
        assert type(candidate_pid) is int
        assert after_runtime.get("handoff_capabilities") == [
            handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ]
        candidate_counters = after_runtime.get("counters")
        assert isinstance(candidate_counters, dict)
        drain_rejections_before = candidate_counters.get("responses_rejected_while_draining")
        assert type(drain_rejections_before) is int
        _assert_same_runtime_identity(upgraded, after)
        assert candidate_pid != previous_pid
        assert (
            run_candidate(
                "doctor",
                *command_output,
            )["ok"]
            is True
        )
        for relative in previous_paths - current_paths:
            assert not install.joinpath(*PurePosixPath(relative).parts).exists()
        upstream.push((200, b'{"id":"after","status":"completed"}'))
        assert (
            native_release.post_response(port, route="upstream")
            == b'{"id":"after","status":"completed"}'
        )

        current_selection = generation.read(ctx)
        assert current_selection is not None
        assert current_selection.predecessor is not None
        retained = payload_rollback.load_retained(ctx)
        assert retained.predecessor.release_receipt_sha256 == published_predecessor.receipt_sha256
        assert (
            retained.predecessor.serving_payload_sha256
            == published_predecessor.serving_payload_sha256
        )
        installed_before_recovery = Path(records.installed_path(ctx)).read_bytes()
        command_before_recovery = Path(ctx.command).resolve(strict=True)
        reverse = payload_transaction.begin_rollback_transaction(
            ctx,
            payload_rollback.load_retained(ctx),
        )
        reverse.commit_projection()
        reverse.preserve_for_recovery("rollback controller outcome unknown")
        rollback_snapshot = Path(records.transaction_root(ctx), "rollback")
        Path(rollback_snapshot, command.SNAPSHOT_FILENAME).unlink()
        rollback_snapshot.rmdir()

        recovered = run_candidate(
            "recover",
            *command_output,
        )
        assert recovered == {
            "transaction_id": current_selection.predecessor,
            "version": previous_version,
            "state": "rolled_back",
            "next": "openai-responses-proxy status",
        }
        assert generation.read(ctx) == current_selection
        assert Path(records.installed_path(ctx)).read_bytes() == (installed_before_recovery)
        assert Path(ctx.command).resolve(strict=True) == command_before_recovery
        assert not Path(records.transaction_root(ctx)).exists()
        recovered_status = run_candidate(
            "status",
            *command_output,
        )
        _assert_same_runtime_identity(after, recovered_status)

        rollback_hold_started = threading.Event()
        rollback_release = threading.Event()
        traffic_stop = threading.Event()
        traffic_bodies: list[bytes] = []
        traffic_failures: list[str] = []

        def held_during_rollback(handler) -> None:
            rollback_hold_started.set()
            rollback_release.wait(timeout=60)
            payload = b'{"id":"rollback-held","status":"completed"}'
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        def exercise_admission() -> None:
            while not traffic_stop.is_set():
                upstream.push((200, b'{"id":"traffic","status":"completed"}'))
                try:
                    traffic_bodies.append(
                        native_release.post_response(port, timeout=10, route="upstream")
                    )
                except urllib.error.HTTPError as error:  # pragma: no cover - asserted by parent
                    with error:
                        detail = error.read().decode("utf-8", errors="replace")
                    traffic_failures.append(f"HTTP {error.code}: {detail}")
                    return
                except Exception as error:  # pragma: no cover - asserted by parent
                    traffic_failures.append(f"{error.__class__.__name__}: {error}")
                    return

        upstream.push(held_during_rollback)
        rollback_held: dict[str, bytes] = {}
        rollback_holder = threading.Thread(
            target=lambda: rollback_held.setdefault(
                "body", native_release.post_response(port, timeout=90, route="upstream")
            ),
            daemon=True,
        )
        rollback_holder.start()
        cleanups.callback(rollback_release.set)
        assert rollback_hold_started.wait(timeout=20)

        traffic = threading.Thread(target=exercise_admission, daemon=True)
        traffic.start()
        cleanups.callback(traffic_stop.set)
        assert wait_until(lambda: len(traffic_bodies) >= 3, timeout=20)
        completed_before_rollback = len(traffic_bodies)

        rollback_future = executor.submit(
            run_candidate,
            "rollback",
            "--to-release",
            previous_version,
            "--port",
            str(port),
            "--timeout-seconds",
            "60",
            "--json",
        )

        def predecessor_serves_new_requests() -> bool:
            runtime = lifecycle_control.read_runtime(ctx)
            return (
                isinstance(runtime, dict)
                and runtime.get("pid") != candidate_pid
                and runtime.get("release") == previous_version
                and runtime.get("accepting") is True
                and runtime.get("draining") is False
            )

        assert wait_until(
            predecessor_serves_new_requests,
            timeout=native_release.COMMAND_TIMEOUT_SECONDS,
        )
        assert wait_until(
            lambda: len(traffic_bodies) > completed_before_rollback,
            timeout=native_release.COMMAND_TIMEOUT_SECONDS,
        )
        traffic_stop.set()
        traffic.join(timeout=20)
        rollback_release.set()
        rollback_holder.join(timeout=60)
        rolled_back = rollback_future.result(timeout=90)

        assert not traffic.is_alive()
        assert not rollback_holder.is_alive()
        assert traffic_bodies
        assert set(traffic_bodies) == {b'{"id":"traffic","status":"completed"}'}
        assert traffic_failures == []
        assert rollback_held == {"body": b'{"id":"rollback-held","status":"completed"}'}
        assert rolled_back["state"] == "rolled_back"
        assert rolled_back["from_release"] == current_version
        assert rolled_back["to_release"] == previous_version
        assert Path(generation.control_context(ctx).executable) == control_executable
        assert command.status(installed_command, control_executable)["state"] == "owned"
        after_rollback = run_installed(
            "status",
            *command_output,
        )
        assert after_rollback["release"] == previous_version
        assert after_rollback["payload_transaction"] is None
        _assert_same_runtime_identity(rolled_back, after_rollback)
        predecessor_runtime = after_rollback.get("runtime")
        assert isinstance(predecessor_runtime, dict)
        assert (
            predecessor_runtime.get("release_receipt_sha256")
            == published_predecessor.receipt_sha256
        )
        assert (
            predecessor_runtime.get("serving_payload_sha256")
            == published_predecessor.serving_payload_sha256
        )
        predecessor_counters = predecessor_runtime.get("counters")
        assert isinstance(predecessor_counters, dict)
        assert (
            predecessor_counters.get("responses_rejected_while_draining") == drain_rejections_before
        )
        assert after_rollback["rollback"] == {
            "state": "available",
            "from_release": previous_version,
            "to_release": current_version,
        }
        rollback_selection = generation.read(ctx)
        rollback_runtime = after_rollback.get("runtime")
        assert rollback_selection is not None
        assert isinstance(rollback_runtime, dict)
        repeated = run_installed(
            "rollback",
            "--to-release",
            previous_version,
            "--port",
            str(port),
            "--timeout-seconds",
            "60",
            "--json",
        )
        assert repeated == {
            "state": "unchanged",
            "release": previous_version,
            "next": "openai-responses-proxy status",
        }
        assert generation.read(ctx) == rollback_selection
        repeated_status = run_installed(
            "status",
            *command_output,
        )
        _assert_same_runtime_identity(after_rollback, repeated_status)
        assert run_installed(
            "recover",
            *command_output,
        ) == {"state": "not_required", "next": None}

        restored = run_installed(
            "rollback",
            "--to-release",
            current_version,
            "--port",
            str(port),
            "--timeout-seconds",
            "60",
            "--json",
        )
        assert restored["state"] == "rolled_back"
        assert restored["from_release"] == previous_version
        assert restored["to_release"] == current_version
        restored_status = run_installed(
            "status",
            *command_output,
        )
        assert restored_status["release"] == current_version
        assert restored_status["payload_transaction"] is None
        _assert_same_runtime_identity(restored, restored_status)

        reloaded = run_candidate(
            "reload",
            "--port",
            str(port),
            "--timeout-seconds",
            "60",
            "--json",
        )
        assert reloaded["new_pid"] != after_runtime.get("pid")
        owned_contexts = (*verification.owned_runtime_contexts(ctx), ctx)
        removed = run_candidate(
            "uninstall",
            "--port",
            str(port),
            "--purge",
            "--json",
        )
        assert set(removed) == {"command_removed", "next", "state", "stopped"}
        assert removed["next"] is None
        assert removed["command_removed"] is True
        assert removed["state"] == "purged"
        assert type(removed["stopped"]) is int
        assert removed["stopped"] >= 0
        projection = native_release.native_service_projection(ctx)
        assert projection["status"] == "absent"
        assert projection["configured_executable"] is None
        assert all(
            native_release.native_service_projection(owned_ctx)["processes"] == []
            for owned_ctx in owned_contexts
        )
        assert not install.exists()
        assert not records.transaction_root(ctx).exists()
        assert process.listener_pids(port) == []
        assert process.listener_pids(runtime_config.DEFAULT_PORT) == canonical_before
