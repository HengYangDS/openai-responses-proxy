"""Native adapter selection and exact owned-process termination contracts."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import native_service


class _NativeModuleFixture(ModuleType):
    """Minimal platform-module fixture for shared native ownership tests."""

    def install(self, _ctx: runtime_spec.NativeServiceContext) -> None:
        pass

    def configured_executable(self, ctx: runtime_spec.NativeServiceContext) -> str:
        return ctx.executable

    def uninstall(self, _ctx: runtime_spec.NativeServiceContext) -> None:
        pass

    def status(self, _ctx: runtime_spec.NativeServiceContext) -> str:
        return "running"


def test_native_service_selection_is_total_for_supported_hosts(subtests, *, mocker) -> None:
    for platform, expected in (
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
        ("win32", "windows"),
        ("cygwin", "windows"),
    ):
        mocker.patch.object(sys, "platform", platform)
        with subtests.test(platform=platform):
            assert native_service.adapter().__name__.rsplit(".", 1)[-1] == expected
    mocker.patch.object(sys, "platform", "plan9")
    with pytest.raises(RuntimeError, match="unsupported platform: plan9"):
        native_service.adapter()


def test_native_service_selection_reports_incomplete_product(*, mocker) -> None:
    real_import = __import__

    def missing_adapters(name, *args, **kwargs):
        if name.endswith("lifecycle.supervision"):
            raise ImportError("missing bundled adapter")
        return real_import(name, *args, **kwargs)

    mocker.patch("builtins.__import__", side_effect=missing_adapters)
    with pytest.raises(errors.ProductAssemblyError, match="installation is incomplete"):
        native_service._platform_adapters()


def test_cygwin_selection_reports_missing_windows_adapter(*, mocker) -> None:
    real_import = __import__

    def missing_windows(name, globals_=None, locals_=None, fromlist=(), level=0):
        if name.endswith("lifecycle.supervision") and fromlist == ("windows",):
            raise ImportError("missing bundled Windows adapter")
        return real_import(name, globals_, locals_, fromlist, level)

    mocker.patch.object(native_service, "_platform_adapters", return_value=())
    mocker.patch.object(sys, "platform", "cygwin")
    mocker.patch("builtins.__import__", side_effect=missing_windows)
    with pytest.raises(errors.ProductAssemblyError, match="installation is incomplete"):
        native_service.adapter()


@pytest.mark.parametrize(
    "role",
    [
        "--internal-listener",
        "--internal-handoff-child",
        "--internal-watchdog",
        "--internal-prewarm",
    ],
)
def test_retired_generation_stops_each_owned_service_role(role: str, *, mocker) -> None:
    ctx = mocker.Mock(executable="/installed/generations/retired/bin/proxy")
    runtime = native_service._NativeRuntime(_NativeModuleFixture("fixture.native"))
    live = {41}

    def inventory_for(executable, *, roles):
        assert executable == ctx.executable
        return sorted(live) if role in roles else []

    def stop(pid, executable, *, roles, timeout_seconds):
        assert executable == ctx.executable
        assert role in roles
        assert timeout_seconds == 2.5
        live.remove(pid)
        return True

    mocker.patch.object(native_service.process, "pids_naming_executable", inventory_for)
    mocker.patch.object(native_service.process, "terminate_executable", stop)
    assert runtime.terminate_runtime(ctx, timeout_seconds=2.5) == 1
    assert live == set()


def test_native_runtime_owns_exact_private_product_processes(subtests, *, mocker) -> None:
    ctx = mocker.Mock(executable="/installed/openai-responses-proxy")
    implementation = _NativeModuleFixture("fixture.native")
    runtime = native_service._NativeRuntime(implementation)
    mocker.patch.object(native_service.process, "pids_naming_executable", return_value=[41, 73])
    for terminated, inventories, expected, message in (
        (True, [[41, 73], []], 2, None),
        (False, [[41]], None, "exit is unproved"),
        (True, [[41], [73]], None, "processes remain"),
    ):
        with subtests.test(message=message or "success"):
            mocker.patch.object(
                native_service.process, "pids_naming_executable", side_effect=inventories
            )
            terminate = mocker.patch.object(
                native_service.process, "terminate_executable", return_value=terminated
            )
            if message is None:
                assert runtime.terminate_runtime(ctx, timeout_seconds=2.5) == expected
                assert terminate.call_count == 2
            else:
                with pytest.raises(errors.InstallError, match=message):
                    runtime.terminate_runtime(ctx, timeout_seconds=2.5)
            mocker.stopall()
