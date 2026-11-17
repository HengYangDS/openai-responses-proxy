"""Remove the product-owned OpenAI Responses Proxy service and payload."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol
from typing import cast

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.supervision.native_service import adapter
from openai_responses_proxy.lifecycle.transaction import completion as transaction_completion
from openai_responses_proxy.lifecycle.transaction import recovery as transaction_recovery

if TYPE_CHECKING:
    from collections.abc import Sequence


class ServiceAdapter(Protocol):
    """Native supervision operations required by uninstall."""

    def uninstall(
        self, ctx: runtime_context.InstallationContext, executables: Sequence[str]
    ) -> None:
        """Remove the control root's native service and the watchdogs of these executables."""
        ...

    def status(self, ctx: runtime_context.InstallationContext) -> str:
        """Return the native service state for this control root."""
        ...

    def terminate_runtime(
        self,
        ctx: runtime_context.RuntimeContext,
        *,
        timeout_seconds: float,
    ) -> int:
        """Terminate and prove exit of this generation's runtime processes."""
        ...


def _stop_proxy(service: ServiceAdapter, owned: Sequence[runtime_context.RuntimeContext]) -> int:
    """Terminate and prove exit of every runtime process of the generations that the store holds."""
    stopped = 0
    for selected_ctx in owned:
        stopped += service.terminate_runtime(selected_ctx, timeout_seconds=5.0)
    return stopped


def _remove_service(
    service: ServiceAdapter,
    ctx: runtime_context.InstallationContext,
    owned: Sequence[runtime_context.RuntimeContext],
) -> None:
    """Deregister supervision and require its read-only status to prove absence."""
    service.uninstall(ctx, [selected.executable for selected in owned])
    if (state := service.status(ctx)) != "absent":
        raise errors.InstallError(f"watchdog service remains {state}")


def uninstall_product(
    ctx: runtime_context.InstallationContext, *, purge: bool = False
) -> dict[str, object]:
    """Remove owned supervision and optionally purge the verified payload.

    The service is the control root's. The processes that are ended are those of the
    generations that the store holds, and without a selected generation no command is this
    installation's to remove.
    """
    service = cast("ServiceAdapter", adapter())

    transaction_state = records.status(ctx)
    if transaction_state is not None:
        if purge and transaction_state.get("state") == "purged":
            owned = generation.owned_contexts(ctx)
            _remove_service(service, ctx, owned)
            stopped = _stop_proxy(service, owned)

            def unexpected_discard(_candidate: runtime_context.RuntimeContext) -> None:
                raise errors.RecoveryStateError("terminal purge must not repeat native disposal")

            result = transaction_recovery.recover(
                ctx,
                runtime=None,
                # Every owned process has exited: health answers nothing, after a wait as now.
                read_after_wait=lambda: None,
                bind_terminal=lambda _ctx: None,
                discard_native=unexpected_discard,
            )
            return {**result, "stopped": stopped, "command_removed": False}
        if transaction_state.get("state") == "invalid":
            raise errors.RecoveryStateError(
                "payload transaction evidence is invalid; preserve it for diagnosis"
            )
        raise errors.RecoveryRequiredError("complete payload recovery before uninstalling")
    installed = records.read_installed(ctx)
    command_path = (
        Path(records.require_command(installed)) if installed is not None else Path(ctx.command)
    )
    control_ctx = generation.control_context(ctx)
    command_state = (
        command.status(command_path, Path(control_ctx.executable))
        if control_ctx is not None
        else command.status_without_payload(command_path)
    )
    service_state = service.status(ctx)
    selected = generation.selected_context(ctx)
    listeners = process.verified_proxy_listener_pids(selected) if selected is not None else []
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

    owned = generation.owned_contexts(ctx)
    _remove_service(service, ctx, owned)
    stopped = _stop_proxy(service, owned)
    if control_ctx is not None:
        command_removed = command.remove(command_path, Path(control_ctx.executable))
    else:
        command.require_absent(command_path)
        command_removed = False

    if purge:
        transaction_completion.purge(ctx)
    return {
        "state": "purged" if purge else "uninstalled",
        "stopped": stopped,
        "command_removed": command_removed,
    }
