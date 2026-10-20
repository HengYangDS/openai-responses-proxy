"""Native recovery preserves retained transaction snapshot authority."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion
from openai_responses_proxy.lifecycle.transaction import execution
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release.fixtures import ROOT
from tests.release.fixtures import native_service_projection
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
    @pytest.mark.parametrize("replacement", ["snapshot_bytes", "unknown_directory"])
    def test_native_recovery_preserves_transaction_disposal_authority(
        self, tmp_path: Path, replacement: str, *, mocker
    ) -> None:
        """Reject unowned recovery inputs until the operator restores exact state."""
        executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(
            strict=True
        )
        home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        environment = native_process_environment(
            user_home=home, install_root=install, state_root=state
        )
        before = native_service_projection(ctx)
        key, trust, anchor = signing_inputs(tmp_path)
        asset = signed_asset(
            executable.parent,
            tmp_path / "assets",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
            upstream_url="http://127.0.0.1:1",
            key=key,
            trust=trust,
        )
        transaction = execution.begin_transaction(ctx, artifact.admit(asset, trust_anchor=anchor))
        transaction.commit_projection()
        root = records.transaction_root(ctx)
        snapshot = root / "rollback" / command.SNAPSHOT_FILENAME
        original = snapshot.read_bytes()
        interruption = mocker.patch.object(
            completion, "_remove_transaction_root", side_effect=errors.InstallError("interrupted")
        )
        with pytest.raises(errors.InstallError, match="interrupted"):
            transaction.rollback()
        mocker.stop(interruption)
        journal = records.journal_path(ctx).read_bytes()
        retained = root / "operator-directory"
        if replacement == "snapshot_bytes":
            snapshot.write_bytes(b"operator snapshot replacement")
        else:
            retained.mkdir()

        for _ in range(2):
            refused = run_command(
                executable, environment, "recover", "--port", str(port), "--json", expected=2
            )
            error = refused["error"]
            assert isinstance(error, dict)
            assert error["code"] == "lifecycle_error"
            assert records.journal_path(ctx).read_bytes() == journal
            assert native_service_projection(ctx) == before
            assert not install.exists()
            assert not Path(ctx.command).exists()
            if replacement == "snapshot_bytes":
                assert snapshot.read_bytes() == b"operator snapshot replacement"
            else:
                assert retained.is_dir()

        if replacement == "snapshot_bytes":
            snapshot.write_bytes(original)
        else:
            retained.rmdir()
        recovered = run_command(executable, environment, "recover", "--port", str(port), "--json")
        assert recovered["state"] == "rolled_back"
        assert not root.exists()
        assert not records.journal_path(ctx).exists()
        assert native_service_projection(ctx) == before
        assert run_command(executable, environment, "recover", "--port", str(port), "--json") == {
            "state": "not_required",
            "next": None,
        }
