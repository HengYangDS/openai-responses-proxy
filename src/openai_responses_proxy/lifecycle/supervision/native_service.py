"""Select the native user-service implementation for this host."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Protocol
from typing import cast

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.service import runtime as service_runtime

_RUNTIME_ROLES = {
    service_runtime.LISTENER_MODE,
    service_runtime.HANDOFF_CHILD_MODE,
    service_runtime.WATCHDOG_MODE,
    service_runtime.PREWARM_MODE,
}


class NativeServiceAdapter(Protocol):
    """Complete platform supervision surface selected at the host boundary."""

    __name__: str

    def install(self, ctx: runtime_spec.NativeServiceContext) -> None:
        """Install or replace the host-native service definition."""
        ...

    def configured_executable(self, ctx: runtime_spec.NativeServiceContext) -> str | None:
        """Return the executable configured by the host-native service."""
        ...

    def projection_is_current(self, ctx: runtime_spec.NativeServiceContext) -> bool:
        """Return whether the host-native service definition is this release's projection."""
        ...

    def uninstall(self, ctx: runtime_spec.NativeServiceContext) -> None:
        """Remove the exact host-native service owned by this installation."""
        ...

    def status(self, ctx: runtime_spec.NativeServiceContext) -> str:
        """Return the host-native service state for this installation."""
        ...

    def terminate_runtime(
        self,
        ctx: runtime_spec.NativeServiceContext,
        *,
        timeout_seconds: float,
    ) -> int:
        """Terminate and prove exit of this generation's runtime processes."""
        ...


class _NativeRuntime:
    """Expose one native service and its exact product-process ownership."""

    def __init__(self, implementation: ModuleType) -> None:
        self._implementation = implementation
        self.__name__ = implementation.__name__

    def install(self, ctx: runtime_spec.NativeServiceContext) -> None:
        self._implementation.install(ctx)

    def configured_executable(self, ctx: runtime_spec.NativeServiceContext) -> str | None:
        return cast(str | None, self._implementation.configured_executable(ctx))

    def projection_is_current(self, ctx: runtime_spec.NativeServiceContext) -> bool:
        return cast(bool, self._implementation.projection_is_current(ctx))

    def uninstall(self, ctx: runtime_spec.NativeServiceContext) -> None:
        self._implementation.uninstall(ctx)

    def status(self, ctx: runtime_spec.NativeServiceContext) -> str:
        return cast(str, self._implementation.status(ctx))

    def _runtime_pids(self, ctx: runtime_spec.NativeServiceContext) -> list[int]:
        return process.pids_naming_executable(ctx.executable, roles=_RUNTIME_ROLES)

    def terminate_runtime(
        self,
        ctx: runtime_spec.NativeServiceContext,
        *,
        timeout_seconds: float,
    ) -> int:
        pids = self._runtime_pids(ctx)
        for pid in pids:
            if not process.terminate_executable(
                pid,
                ctx.executable,
                roles=_RUNTIME_ROLES,
                timeout_seconds=timeout_seconds,
            ):
                raise errors.InstallError(f"verified runtime process {pid} exit is unproved")
        if remaining := self._runtime_pids(ctx):
            raise errors.InstallError(f"verified runtime processes remain: {remaining}")
        return len(pids)


def _platform_adapters() -> tuple[tuple[str, ModuleType], ...]:
    """Load every supported adapter through imports visible to bundlers."""
    try:
        from openai_responses_proxy.lifecycle.supervision import linux
        from openai_responses_proxy.lifecycle.supervision import macos
        from openai_responses_proxy.lifecycle.supervision import windows
    except ImportError as error:
        raise errors.ProductAssemblyError(
            "product installation is incomplete; reinstall the verified release"
        ) from error
    return (("darwin", macos), ("linux", linux), ("win", windows))


def adapter() -> NativeServiceAdapter:
    """Return the statically bundled supervision module for the current platform."""
    for prefix, implementation in _platform_adapters():
        if sys.platform.startswith(prefix):
            return _NativeRuntime(implementation)
    if sys.platform == "cygwin":
        try:
            from openai_responses_proxy.lifecycle.supervision import windows
        except ImportError as error:
            raise errors.ProductAssemblyError(
                "product installation is incomplete; reinstall the verified release"
            ) from error
        return _NativeRuntime(windows)
    raise errors.UnsupportedPlatformError(f"unsupported platform: {sys.platform}")
