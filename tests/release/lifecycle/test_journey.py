"""Signed fresh installation, recovery and supervision lifecycle."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release.fixtures import ROOT
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import post_response
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import signing_inputs
from tests.release.fixtures import stop_native_supervision
from tests.release.fixtures import write_predecessor_native_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tools.release.verification import cleanup_runtime

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestSignedNativeLifecycle:
    def test_signed_fresh_lifecycle_is_transactional(self, tmp_path: Path) -> None:
        executable_value = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        assert executable_value is not None, "native executable must be supplied by release session"
        executable = Path(executable_value).resolve(strict=True)
        bundle = executable.parent
        home, install, state = (
            tmp_path / "home",
            tmp_path / "payload",
            tmp_path / "state",
        )
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home,
            install_root=install,
            state_root=state,
        )
        assert ctx.service_id != runtime_context.SERVICE_ID
        isolated_before = native_service_projection(ctx)
        assert isolated_before == {
            "service_id": ctx.service_id,
            "status": "absent",
            "configured_executable": None,
            "processes": [],
        }
        canonical_ctx = runtime_context.create()
        canonical_service_before = native_service_projection(canonical_ctx)
        canonical_before = process.listener_pids(runtime_config.DEFAULT_PORT)

        key, trust, anchor = signing_inputs(tmp_path)

        upstream = ScriptedUpstream()
        upstream.start()
        upstream_url = upstream.base_url().replace("127.0.0.1", "localhost")
        current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        current_asset = signed_asset(
            bundle,
            tmp_path / "current-assets",
            version=current_version,
            upstream_url=upstream_url,
            key=key,
            trust=trust,
            preserve_manifest=True,
        )

        with ExitStack() as cleanups:
            cleanups.callback(upstream.close)
            cleanups.callback(cleanup_runtime, ctx)

            installed = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--upstream-base-url",
                upstream_url,
                "--json",
            )
            assert installed["state"] == "installed"
            status = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert status["release"] == current_version
            assert status["service"] == "running"
            installed_runtime = installed.get("runtime")
            assert isinstance(installed_runtime, dict)
            installed_pid = installed_runtime.get("pid")
            assert type(installed_pid) is int
            selected = generation.read(ctx)
            installed_bytes = records.installed_path(ctx).read_bytes()
            supervisor = native_service_projection(ctx)
            repeated = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
            )
            assert repeated == {
                "state": "unchanged",
                "release": current_version,
                "next": "openai-responses-proxy status",
            }
            assert generation.read(ctx) == selected
            assert records.installed_path(ctx).read_bytes() == installed_bytes
            assert native_service_projection(ctx) == supervisor
            assert process.listener_pids(port) == [installed_pid]
            assert records.status(ctx) is None
            assert (
                run_command(executable, environment, "doctor", "--port", str(port), "--json")["ok"]
                is True
            )
            upstream.push((200, b'{"id":"ok","status":"completed"}'))
            assert post_response(port, route="upstream") == b'{"id":"ok","status":"completed"}'
            assert (
                run_command(executable, environment, "reload", "--port", str(port), "--json")[
                    "new_pid"
                ]
                != installed_pid
            )
            upstream.push((200, b'{"id":"after-reload","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-reload","status":"completed"}'
            )
            stop_native_supervision(ctx)
            stopped = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert stopped["state"] == "stopped"
            assert stopped["service"] == "installed"
            assert stopped["runtime"] is None
            diagnosed = run_command(
                executable, environment, "doctor", "--port", str(port), "--json", expected=1
            )
            assert diagnosed["state"] == "stopped"
            assert diagnosed["next"] == "openai-responses-proxy reload"
            started = run_command(executable, environment, "reload", "--port", str(port), "--json")
            assert started["state"] == "started"
            assert process.listener_pids(port) == [started["new_pid"]]
            restored = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert restored["state"] == "running"
            assert restored["service"] == "running"
            upstream.push((200, b'{"id":"after-start","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-start","status":"completed"}'
            )
            stop_native_supervision(ctx, keep_runtime=True)
            unsupervised = run_command(
                executable, environment, "status", "--port", str(port), "--json"
            )
            assert unsupervised["state"] == "degraded"
            assert unsupervised["service"] == "installed"
            serving = unsupervised["runtime"]
            assert isinstance(serving, dict)
            diagnosed = run_command(
                executable, environment, "doctor", "--port", str(port), "--json", expected=1
            )
            assert diagnosed["state"] == "degraded"
            assert diagnosed["next"] == "openai-responses-proxy reload"
            rebound = run_command(executable, environment, "reload", "--port", str(port), "--json")
            assert rebound["state"] == "reloaded"
            assert rebound["old_pid"] == serving["pid"]
            supervised = run_command(
                executable, environment, "status", "--port", str(port), "--json"
            )
            assert supervised["state"] == "running"
            assert supervised["service"] == "running"
            successor = supervised["runtime"]
            assert isinstance(successor, dict)
            assert successor["pid"] == rebound["new_pid"]
            upstream.push((200, b'{"id":"after-rebind","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-rebind","status":"completed"}'
            )
            if sys.platform != "win32":
                write_predecessor_native_projection(ctx)
                outdated = run_command(
                    executable, environment, "status", "--port", str(port), "--json"
                )
                assert outdated["state"] == "degraded"
                assert outdated["service"] == "outdated"
                diagnosed = run_command(
                    executable, environment, "doctor", "--port", str(port), "--json", expected=1
                )
                assert diagnosed["next"] == "openai-responses-proxy reload"
                assert (
                    run_command(executable, environment, "reload", "--port", str(port), "--json")[
                        "state"
                    ]
                    == "reloaded"
                )
                current = run_command(
                    executable, environment, "status", "--port", str(port), "--json"
                )
                assert (current["state"], current["service"]) == ("running", "running")
            stop_native_supervision(ctx, keep_runtime=True)
            orphaned = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert orphaned["service"] == "installed"
            orphan = orphaned["runtime"]
            assert isinstance(orphan, dict)
            adopted = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--upstream-base-url",
                upstream_url,
                "--json",
            )
            assert adopted["state"] == "installed"
            adopted_runtime = adopted["runtime"]
            assert isinstance(adopted_runtime, dict)
            assert adopted_runtime["pid"] == orphan["pid"]
            readopted = run_command(
                executable, environment, "status", "--port", str(port), "--json"
            )
            assert (readopted["state"], readopted["service"]) == ("running", "running")
            removed = run_command(
                executable, environment, "uninstall", "--port", str(port), "--json"
            )
            assert removed["state"] == "uninstalled"
            retained = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert retained["state"] == "degraded"
            assert retained["service"] == "absent"
            assert retained["runtime"] is None
            diagnosed = run_command(
                executable, environment, "doctor", "--port", str(port), "--json", expected=1
            )
            assert diagnosed["next"] == "openai-responses-proxy install --help"
            restored = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--upstream-base-url",
                upstream_url,
                "--json",
            )
            assert restored["state"] == "installed"
            resumed = run_command(executable, environment, "status", "--port", str(port), "--json")
            assert (resumed["state"], resumed["service"]) == ("running", "running")
            upstream.push((200, b'{"id":"after-restore","status":"completed"}'))
            assert post_response(port, route="upstream") == (
                b'{"id":"after-restore","status":"completed"}'
            )
            residue = install / "operator-note.txt"
            residue.write_text("preserve\n", encoding="utf-8")
            refused_purge = run_command(
                executable,
                environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
                expected=2,
            )
            assert refused_purge["error"] == {
                "code": "lifecycle_error",
                "message": "unknown install content remains: operator-note.txt",
                "next": "openai-responses-proxy doctor",
            }
            assert residue.read_text(encoding="utf-8") == "preserve\n"
            assert not records.transaction_root(ctx).exists()
            pending_purge = records.read_journal(ctx)
            assert pending_purge["state"] == "purged"
            refused_install = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
                expected=2,
            )
            assert refused_install["error"] == {
                "code": "lifecycle_error",
                "message": (
                    "installed payload root contains unverified content; "
                    "remove it explicitly before installing"
                ),
                "next": "openai-responses-proxy doctor",
            }
            assert not records.transaction_root(ctx).exists()
            residue.unlink()
            recovered_purge = run_command(
                executable, environment, "recover", "--port", str(port), "--json"
            )
            assert recovered_purge == {
                "state": "purged",
                "transaction_id": pending_purge["transaction_id"],
                "version": current_version,
                "stopped": 0,
                "command_removed": False,
                "next": "openai-responses-proxy status",
            }
            assert not install.exists()
            assert not records.journal_path(ctx).exists()

            reinstalled = run_command(
                executable,
                environment,
                "install",
                "--asset",
                str(current_asset),
                "--trust-anchor",
                str(anchor),
                "--port",
                str(port),
                "--json",
            )
            assert reinstalled["state"] == "installed"
            reinstalled_status = run_command(
                executable,
                environment,
                "status",
                "--port",
                str(port),
                "--json",
            )
            assert reinstalled_status["release"] == current_version
            assert reinstalled_status["service"] == "running"
            run_command(
                executable,
                environment,
                "uninstall",
                "--port",
                str(port),
                "--purge",
                "--json",
            )
            assert not install.exists()

            payload_transaction.begin_transaction(
                ctx,
                artifact.admit(current_asset, trust_anchor=anchor),
            )
            transaction_id = records.read_journal(ctx)["transaction_id"]
            recovered = run_command(
                executable, environment, "recover", "--port", str(port), "--json"
            )
            assert recovered == {
                "state": "closed",
                "transaction_id": transaction_id,
                "version": current_version,
                "next": "openai-responses-proxy status",
            }
            assert not records.transaction_root(ctx).exists()
            assert not records.journal_path(ctx).exists()
        assert native_service_projection(ctx) == isolated_before
        assert native_service_projection(canonical_ctx) == canonical_service_before
        assert process.listener_pids(runtime_config.DEFAULT_PORT) == canonical_before
