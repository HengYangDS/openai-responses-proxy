"""Remove the product-owned OpenAI Responses Proxy service and payload."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from typing import cast

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import state
from openai_responses_proxy.lifecycle import transaction
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.supervision.native_service import adapter


class ServiceAdapter(Protocol):
    """Native supervision operations required by uninstall."""

    def uninstall(self, ctx: runtime_context.RuntimeContext) -> None:
        """Remove the exact native service owned by this runtime context."""
        ...

    def status(self, ctx: runtime_context.RuntimeContext) -> str:
        """Return the native service state for this runtime context."""
        ...

    def terminate_runtime(
        self,
        ctx: runtime_context.RuntimeContext,
        *,
        timeout_seconds: float,
    ) -> int:
        """Terminate and prove exit of this generation's runtime processes."""
        ...


def _stop_proxy(service: ServiceAdapter, ctx: runtime_context.RuntimeContext) -> int:
    """Terminate and prove exit of every runtime process owned by this installation."""
    contexts = (*generation.owned_contexts(ctx), ctx)
    unique = {selected.executable: selected for selected in contexts}
    stopped = 0
    for selected_ctx in unique.values():
        stopped += service.terminate_runtime(selected_ctx, timeout_seconds=5.0)
    return stopped


def _remove_service(service: ServiceAdapter, ctx: runtime_context.RuntimeContext) -> None:
    """Deregister supervision and require its read-only status to prove absence."""
    service.uninstall(ctx)
    if (state := service.status(ctx)) != "absent":
        raise errors.InstallError(f"watchdog service remains {state}")


def uninstall_product(
    ctx: runtime_context.RuntimeContext, *, purge: bool = False
) -> dict[str, object]:
    """Remove owned supervision and optionally purge the verified payload."""
    service = cast(ServiceAdapter, adapter())

    transaction_state = state.status(ctx)
    if transaction_state is not None:
        if purge and transaction_state.get("state") == "purged":
            _remove_service(service, ctx)
            stopped = _stop_proxy(service, ctx)

            def unexpected_discard(_candidate: runtime_context.RuntimeContext) -> None:
                raise errors.RecoveryStateError("terminal purge must not repeat native disposal")

            result = transaction.recover(
                ctx,
                runtime=None,
                bind_terminal=lambda _ctx: None,
                discard_native=unexpected_discard,
            )
            return {**result, "stopped": stopped, "command_removed": False}
        if transaction_state.get("state") == "invalid":
            raise errors.RecoveryStateError(
                "payload transaction evidence is invalid; preserve it for diagnosis"
            )
        raise errors.RecoveryRequiredError("complete payload recovery before uninstalling")
    installed = state.read_installed(ctx)
    command_path = (
        Path(state.require_command(installed)) if installed is not None else Path(ctx.command)
    )
    control_ctx = generation.control_context(ctx)
    command_state = command.status(command_path, Path(control_ctx.executable))
    service_state = service.status(ctx)
    listeners = process.verified_proxy_listener_pids(ctx)
    install_root = Path(ctx.install_dir)
    if (
        installed is None
        and not install_root.exists()
        and not install_root.is_symlink()
        and service_state == "absent"
        and not listeners
        and command_state.get("state") == "absent"
    ):
        return {
            "state": "not_installed",
            "stopped": 0,
            "command_removed": False,
        }

    _remove_service(service, ctx)
    stopped = _stop_proxy(service, ctx)
    command_removed = command.remove(command_path, Path(control_ctx.executable))

    if purge:
        transaction.purge(ctx)
    return {
        "state": "purged" if purge else "uninstalled",
        "stopped": stopped,
        "command_removed": command_removed,
    }
