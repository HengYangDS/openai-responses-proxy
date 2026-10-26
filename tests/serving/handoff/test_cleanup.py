"""Source-only contracts for exact subprocess fixture cleanup."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from tests.serving.handoff.fixtures import terminate_owned_proxy
from tests.serving.handoff.fixtures import write_installed_payload
from tests.serving.handoff.subprocess import fixtures as subprocess_fixtures
from tests.serving.handoff.subprocess.fixtures import SubprocessFixture
from tools.release import verification


class TestHandoffCleanup(SubprocessFixture):
    def test_detached_fixture_stays_beneath_the_surviving_executor_root(
        self, *, tmp_path, monkeypatch, mocker
    ) -> None:
        root = tmp_path / "owned"
        root.mkdir()
        contexts = tmp_path / "contexts.jsonl"
        contexts.touch()
        monkeypatch.setenv(verification._TEST_ROOT, str(root))
        monkeypatch.setenv(verification._CONTEXTS, str(contexts))
        failure = RuntimeError("stop before runtime effects")
        allocated = []

        def stop(candidate, **_kwargs):
            allocated.append(candidate)
            raise failure

        mocker.patch.object(subprocess_fixtures, "write_installed_payload", side_effect=stop)
        with pytest.raises(RuntimeError) as raised:
            self._installed_fixture(release="test", port=43210, upstream_url="http://127.0.0.1")

        assert raised.value is failure
        assert len(allocated) == 1
        assert allocated[0].is_relative_to(root)
        assert not allocated[0].exists()
        assert contexts.read_bytes() == b""

    @pytest.mark.parametrize("partial_payload", [False, True])
    def test_failed_fixture_initialization_removes_its_temporary_payload(
        self, *, mocker, tmp_path, partial_payload
    ) -> None:
        roots = []
        failure = RuntimeError("payload initialization failed")
        mocker.patch.object(subprocess_fixtures.tempfile, "tempdir", str(tmp_path))

        def fail_initialization(root, **_kwargs):
            roots.append(root)
            if partial_payload:
                payload = root / "payload"
                payload.mkdir()
                (payload / "partial").write_text("incomplete", encoding="utf-8")
            raise failure

        mocker.patch.object(
            subprocess_fixtures, "write_installed_payload", side_effect=fail_initialization
        )

        with pytest.raises(RuntimeError) as raised:
            self._installed_fixture(release="test", port=0, upstream_url="http://127.0.0.1")

        assert raised.value is failure
        assert len(roots) == 1
        assert roots[0].parent == tmp_path
        assert not roots[0].exists()

    def test_fixture_cleanup_terminates_before_removing_temporary_payload(self, *, mocker) -> None:
        events = []
        temporary = tempfile.TemporaryDirectory()
        cleanup = temporary.cleanup
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )

        service = mocker.Mock()
        service.uninstall.side_effect = lambda _ctx: events.append("service-uninstall")
        service.status.side_effect = lambda _ctx: events.append("service-status") or "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)

        def inventory(_proxy_script):
            events.append("inventory")
            return [123] if events.count("inventory") == 1 else []

        try:
            mocker.patch.object(process, "pids_naming_executable", side_effect=inventory)
            captured = process.OwnedProcess(123, "/tmp/owned/proxy.py", 42.0)
            mocker.patch.object(
                process,
                "capture_executable",
                side_effect=lambda *_args, **_kwargs: events.append("capture") or captured,
            )
            mocker.patch.object(
                process,
                "terminate_owned_process",
                side_effect=lambda *_args, **_kwargs: events.append("terminate") or True,
            )
            mocker.patch.object(process, "owned_process_alive", return_value=False)
            mocker.patch.object(temporary, "cleanup", side_effect=lambda: events.append("cleanup"))
            self._cleanup_installed_fixture(temporary, ctx, {})
        finally:
            cleanup()

        assert events == [
            "service-uninstall",
            "service-status",
            "inventory",
            "capture",
            "terminate",
            "cleanup",
        ]

    def test_fixture_cleanup_retries_a_transient_native_payload_lock(self, *, mocker) -> None:
        temporary = mocker.Mock()
        temporary.cleanup.side_effect = [PermissionError("mapped module"), None]
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )
        service = mocker.Mock()
        service.status.return_value = "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)
        mocker.patch.object(process, "pids_naming_executable", return_value=[])

        self._cleanup_installed_fixture(temporary, ctx, {})

        assert temporary.cleanup.call_count == 2

    def test_fixture_cleanup_uses_the_observed_successor_when_inventory_misses_it(
        self, *, mocker
    ) -> None:
        temporary = mocker.Mock()
        owned = process.OwnedProcess(123, "/tmp/owned/proxy.py", 42.0)
        ctx = runtime_context.RuntimeContext(
            install_dir="/tmp/owned",
            executable="/tmp/owned/proxy.py",
            command="/tmp/owned/proxy",
            log_dir="/tmp/owned/state",
        )
        service = mocker.Mock()
        service.status.return_value = "absent"
        mocker.patch.object(native_service, "adapter", return_value=service)
        mocker.patch.object(process, "pids_naming_executable", return_value=[])
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        alive = mocker.patch.object(process, "owned_process_alive", return_value=False)

        self._cleanup_installed_fixture(temporary, ctx, {123: owned})

        terminate.assert_called_once_with(owned)
        alive.assert_called_once_with(owned)
        temporary.cleanup.assert_called_once_with()

    def test_owned_child_cleanup_is_bound_to_its_temporary_proxy_path(self, *, mocker) -> None:
        proxy_script = "/tmp/owned/.codex/responses-proxy/proxy.py"
        owns_pid = mocker.patch.object(process, "pid_names_executable", return_value=True)
        terminate = mocker.patch.object(process, "terminate_executable", return_value=True)
        terminate_owned_proxy(123, proxy_script)

        owns_pid.assert_called_once()
        terminate.assert_called_once()

    def test_owned_child_cleanup_reports_an_unconfirmed_exit(self, *, mocker) -> None:
        proxy_script = "/tmp/owned/.codex/responses-proxy/proxy.py"
        command = f"python {proxy_script} --handoff-child"
        mocker.patch.object(process, "pid_names_executable", return_value=True)
        mocker.patch.object(process, "process_command", return_value=command)
        mocker.patch.object(process, "terminate_executable", return_value=False)
        with pytest.raises(RuntimeError, match="owned proxy child 123 did not terminate"):
            terminate_owned_proxy(123, proxy_script)


def test_installed_payload_retains_complete_custody_before_native_prewarm(
    tmp_path: Path, monkeypatch, mocker
) -> None:
    """The surviving executor sees the exact generation before a child can exist."""
    root = tmp_path / "owned"
    root.mkdir()
    contexts = tmp_path / "contexts.jsonl"
    contexts.touch()
    monkeypatch.setenv(verification._TEST_ROOT, str(root))
    monkeypatch.setenv(verification._CONTEXTS, str(contexts))
    source = (
        tmp_path / "candidate" / "bin" / product_identity.executable_name(windows=os.name == "nt")
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"native candidate fixture")
    library = source.parent / "_internal" / "runtime.dat"
    library.parent.mkdir()
    library.write_bytes(b"complete frozen dependency")
    monkeypatch.setenv("OPENAI_RESPONSES_PROXY_EXECUTABLE", str(source))
    observed = []

    def prewarm(ctx):
        observed.append(ctx)
        rows = contexts.read_bytes().splitlines()
        assert len(rows) == 1, "complete cleanup custody must precede native execution"
        assert json.loads(rows[0]) == asdict(ctx)

    from openai_responses_proxy.lifecycle import candidate

    mocker.patch.object(candidate, "prewarm", side_effect=prewarm)
    ctx = write_installed_payload(
        root / "fixture", release="test", port=43210, upstream_url="http://127.0.0.1:43211"
    )

    assert observed == [ctx]
    assert ctx.port == 43210
    assert Path(ctx.executable).is_file()
    manifest = json.loads(projection.payload_manifest_path(ctx).read_bytes())
    assert manifest["files"]["bin/_internal/runtime.dat"]
    selection = generation.read(ctx)
    assert selection is not None
    assert selection.active == "0" * 32
