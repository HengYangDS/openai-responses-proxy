"""Native adapter selection and exact owned-process termination contracts."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import native_service

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import runtime_spec


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


def test_native_service_selection_is_total_for_supported_hosts(
    subtests: pytest.Subtests, *, mocker: MockerFixture
) -> None:
    for platform, expected in (
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
        ("win32", "windows"),
    ):
        mocker.patch.object(sys, "platform", platform)
        with subtests.test(platform=platform):
            assert native_service.adapter().__name__.rsplit(".", 1)[-1] == expected
    mocker.patch.object(sys, "platform", "plan9")
    with pytest.raises(RuntimeError, match="unsupported platform: plan9"):
        native_service.adapter()


def test_native_service_selection_reports_incomplete_product(*, mocker: MockerFixture) -> None:
    real_import = __import__

    def missing_adapters(
        name: str,
        module_globals: Mapping[str, object] | None = None,
        module_locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name.endswith("lifecycle.supervision"):
            raise ImportError("missing bundled adapter")
        return real_import(name, module_globals, module_locals, fromlist, level)

    mocker.patch("builtins.__import__", side_effect=missing_adapters)
    with pytest.raises(errors.ProductAssemblyError, match="installation is incomplete"):
        native_service._platform_adapters()


@pytest.mark.parametrize(
    "role",
    [
        "--internal-listener",
        "--internal-handoff-child",
        "--internal-watchdog",
        "--internal-prewarm",
    ],
)
def test_retired_generation_stops_each_owned_service_role(
    role: str, *, mocker: MockerFixture
) -> None:
    ctx = mocker.Mock(executable="/installed/generations/retired/bin/proxy")
    runtime = native_service._NativeRuntime(_NativeModuleFixture("fixture.native"))
    live = {41}

    def inventory_for(
        expected_path: str, *, roles: set[str] | frozenset[str] | None = None
    ) -> list[int]:
        assert expected_path == ctx.executable
        assert roles is not None
        return sorted(live) if role in roles else []

    def stop(
        pid: int,
        expected_path: str,
        *,
        roles: set[str] | frozenset[str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> bool:
        assert expected_path == ctx.executable
        assert roles is not None
        assert role in roles
        assert timeout_seconds == 2.5
        live.remove(pid)
        return True

    mocker.patch.object(native_service.process, "pids_naming_executable", inventory_for)
    mocker.patch.object(native_service.process, "terminate_executable", stop)
    assert runtime.terminate_runtime(ctx, timeout_seconds=2.5) == 1
    assert live == set()


def test_native_runtime_owns_exact_private_product_processes(
    subtests: pytest.Subtests, *, mocker: MockerFixture
) -> None:
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
