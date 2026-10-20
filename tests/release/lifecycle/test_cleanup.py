"""Native teardown across control-flow failure and process ownership."""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release import fixtures as release_fixtures
from tests.release.fixtures import ROOT
from tests.release.fixtures import cleanup_runtime
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
    @pytest.mark.parametrize(
        "interruption", [None, AssertionError, RuntimeError, TimeoutError, KeyboardInterrupt]
    )
    def test_native_teardown_survives_test_control_flow_failures(
        self,
        tmp_path: Path,
        interruption: type[BaseException] | None,
    ) -> None:
        """Purge the isolated installation even when test control flow aborts."""
        executable_value = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        assert executable_value is not None, "native executable must be supplied by release session"
        executable = Path(executable_value).resolve(strict=True)
        workspace = TemporaryDirectory(dir=tmp_path, delete=False)
        with workspace as directory:
            root = Path(directory)
            home, install, state = (
                root / "home",
                root / "payload",
                root / "state",
            )
            home.mkdir()
            port = free_port()
            ctx = runtime_context_for(home, install, state, port)
            environment = native_process_environment(
                user_home=home,
                install_root=install,
                state_root=state,
            )
            isolated_before = native_service_projection(ctx)
            canonical_ctx = runtime_context.create()
            canonical_before = native_service_projection(canonical_ctx)

            key, trust, anchor = signing_inputs(root)
            current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
            current_asset = signed_asset(
                executable.parent,
                root / "current-assets",
                version=current_version,
                upstream_url="http://127.0.0.1:1",
                key=key,
                trust=trust,
            )

            owned_contexts: list[runtime_context.RuntimeContext] = []

            def interrupt_installed_lifecycle() -> None:
                with ExitStack() as cleanups:
                    cleanups.callback(cleanup_runtime, ctx)
                    cleanups.callback(
                        run_command,
                        executable,
                        environment,
                        "uninstall",
                        "--port",
                        str(port),
                        "--purge",
                        "--json",
                    )
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
                        "--json",
                    )
                    assert installed["state"] == "installed"
                    owned_contexts.extend(generation.owned_contexts(ctx))
                    if interruption is not None:
                        raise interruption("simulated test control-flow failure")

            if interruption is None:
                interrupt_installed_lifecycle()
            else:
                with pytest.raises(interruption):
                    interrupt_installed_lifecycle()

            assert native_service_projection(ctx) == isolated_before
            assert native_service_projection(canonical_ctx) == canonical_before
            assert owned_contexts
            assert all(
                native_service_projection(owned)["processes"] == [] for owned in owned_contexts
            )
            assert all(
                not path.exists() and not path.is_symlink()
                for path in (
                    install,
                    Path(ctx.command),
                    records.journal_path(ctx),
                    records.transaction_root(ctx),
                )
            )
        workspace.cleanup()
        assert not Path(directory).exists()


def test_native_cleanup_separates_registration_and_process_ownership(
    tmp_path: Path, *, mocker
) -> None:
    """Teardown removes the stable service after its generation leaves the selector."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    first = generation.context(ctx, "1" * 32)
    second = generation.context(ctx, "2" * 32)
    for owned in (first, second):
        Path(owned.payload_dir).mkdir(parents=True)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.side_effect = [second.executable, None]
    mocker.patch.object(release_fixtures.native_service, "adapter", return_value=service)
    mocker.patch.object(release_fixtures.process, "pids_naming_executable", return_value=[])

    cleanup_runtime(ctx)

    service.uninstall.assert_called_once_with(ctx)


def test_native_cleanup_continues_independent_processes_after_unproved_exit(
    tmp_path: Path, *, mocker
) -> None:
    """Retain the safe failure while still stopping the second owned generation."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.return_value = None
    mocker.patch.object(release_fixtures.native_service, "adapter", return_value=service)
    mocker.patch.object(release_fixtures, "_process_contexts", return_value=(ctx,))
    mocker.patch.object(release_fixtures.process, "pids_naming_executable", return_value=[41, 73])
    failure = errors.InstallError(
        "native process termination wait: access denied; exit is unproved"
    )
    terminate = mocker.patch.object(
        release_fixtures.process, "terminate_executable", side_effect=[failure, True]
    )

    with pytest.raises(errors.InstallError) as raised:
        cleanup_runtime(ctx)

    assert raised.value is failure
    assert [call.args[0] for call in terminate.call_args_list] == [41, 73]
