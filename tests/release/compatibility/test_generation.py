"""Released-predecessor compensation preserves terminal authority during disposal."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import execution
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release import fixtures as native_release
from tests.release.compatibility.fixtures import _assert_same_runtime_identity
from tests.release.compatibility.fixtures import _required_path
from tests.release.compatibility.fixtures import admitted_predecessor
from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tools.release import verification

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


@pytest.mark.parametrize("phase", ["materialized", "activated"])
def test_native_compensation_resumes_partial_candidate_disposal(tmp_path: Path, phase: str) -> None:
    """A fresh native process cleans only recorded candidate residue, not the predecessor."""
    executable = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
    bundle = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_BUNDLE")
    previous_asset = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET")
    previous_trust = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR")
    version = (native_release.ROOT / "VERSION").read_text(encoding="ascii").strip()
    _previous, previous_version, previous_executable = admitted_predecessor(
        tmp_path,
        previous_asset=previous_asset,
        previous_trust=previous_trust,
        current_version=version,
    )
    home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
    home.mkdir()
    port = free_port()
    ctx = native_release.runtime_context_for(home, install, state, port)
    environment = native_process_environment(user_home=home, install_root=install, state_root=state)
    service_before = native_release.native_service_projection(ctx)
    key, trust, anchor = native_release.signing_inputs(tmp_path)
    upstream = ScriptedUpstream()
    upstream.start()
    output = ("--port", str(port), "--json")

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
            upstream.base_url(),
            *output,
        )
        assert installed["state"] == "installed"
        before = native_release.run_command(executable, environment, "status", *output)
        assert before["release"] == previous_version
        selection = generation.read(ctx)
        assert selection is not None
        metadata = records.installed_path(ctx).read_bytes()
        command_before = command.snapshot(
            Path(ctx.command), Path(generation.control_context(ctx).executable)
        )
        supervision = native_release.native_service_projection(ctx)
        current = native_release.signed_asset(
            bundle,
            tmp_path / "current-assets",
            version=version,
            upstream_url=upstream.base_url(),
            key=key,
            trust=trust,
        )
        transaction = execution.begin_transaction(ctx, artifact.admit(current, trust_anchor=anchor))
        transaction.commit_projection()
        if phase == "activated":
            transaction.activate()
        candidate = Path(transaction.context.payload_dir)
        residue = candidate / "operator-directory"
        residue.mkdir()
        cleanups.callback(lambda: residue.rmdir() if residue.exists() else None)

        with pytest.raises(errors.InstallError, match=r"unknown.*content"):
            transaction.rollback()

        journal = records.journal_path(ctx).read_bytes()
        assert records.read_journal(ctx)["state"] == "rolled_back"
        assert not Path(transaction.context.executable).exists()
        assert tuple(candidate.iterdir()) == (residue,)
        assert residue.is_dir()
        assert generation.read(ctx) == selection
        assert records.installed_path(ctx).read_bytes() == metadata
        restored_command = command.snapshot(
            Path(ctx.command), Path(generation.control_context(ctx).executable)
        )
        for field in ("state", "path", "target", "kind"):
            assert getattr(restored_command, field) == getattr(command_before, field)
        if phase == "materialized":
            assert restored_command == command_before
        assert native_release.native_service_projection(ctx) == supervision

        refused = native_release.run_command(
            executable, environment, "recover", *output, expected=2
        )
        error = refused["error"]
        assert isinstance(error, dict)
        assert error["code"] == "lifecycle_error"
        assert residue.is_dir()
        assert records.journal_path(ctx).read_bytes() == journal
        assert generation.read(ctx) == selection
        assert records.installed_path(ctx).read_bytes() == metadata
        assert (
            command.snapshot(Path(ctx.command), Path(generation.control_context(ctx).executable))
            == restored_command
        )
        assert native_release.native_service_projection(ctx) == supervision
        residue.rmdir()
        recovered = native_release.run_command(executable, environment, "recover", *output)
        assert recovered["state"] == "rolled_back"
        assert not candidate.exists()
        assert records.status(ctx) is None
        assert not records.transaction_root(ctx).exists()
        assert generation.read(ctx) == selection
        assert records.installed_path(ctx).read_bytes() == metadata
        assert (
            command.snapshot(Path(ctx.command), Path(generation.control_context(ctx).executable))
            == restored_command
        )
        assert native_release.native_service_projection(ctx) == supervision
        after = native_release.run_command(executable, environment, "status", *output)
        _assert_same_runtime_identity(before, after)
        upstream.push((200, b'{"id":"retained","status":"completed"}'))
        assert native_release.post_response(port, route="upstream") == (
            b'{"id":"retained","status":"completed"}'
        )
        assert native_release.run_command(executable, environment, "recover", *output) == {
            "state": "not_required",
            "next": None,
        }
    assert native_release.native_service_projection(ctx) == service_before
