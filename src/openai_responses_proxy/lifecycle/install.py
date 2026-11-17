"""Verify and transactionally install one signed native release asset."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import identity

if TYPE_CHECKING:
    from collections.abc import Mapping


def _lacks_supervision(
    active: runtime_context.RuntimeContext,
    released: artifact.VerifiedArtifact,
    installed: Mapping[str, object],
    observed: Mapping[str, object],
) -> bool:
    """Return whether this artifact's verified installation lacks only running supervision.

    Its listener either serves this exact artifact or is absent with the port free.
    """
    integrity = observed.get("payload_integrity")
    command_state = observed.get("command")
    if (
        not isinstance(integrity, dict)
        or integrity.get("ok") is not True
        or observed.get("service") not in {"absent", "installed"}
        or not isinstance(command_state, dict)
        or command_state.get("state") not in {"owned", "absent"}
    ):
        return False
    committed = identity.committed_payload(Path(active.executable))
    if (
        committed is None
        or committed.release_receipt_sha256 != released.receipt_sha256
        or committed.serving_payload_sha256 != released.serving_payload_sha256
        or not control.installed_names(active, installed, committed)
    ):
        return False
    runtime = observed.get("runtime")
    if isinstance(runtime, dict):
        serving = (runtime.get("release_receipt_sha256"), runtime.get("serving_payload_sha256"))
        return serving == (released.receipt_sha256, released.serving_payload_sha256)
    return observed.get("listener_pids") == [] and not process.listener_pids(active.port)


def install_asset(
    ctx: runtime_context.InstallationContext,
    asset: Path,
    *,
    trust_anchor: Path,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Install one verified native asset through the platform service adapter."""
    try:
        released = artifact.admit(asset, trust_anchor=trust_anchor)
    except errors.InstallError as exc:
        raise errors.InstallInputError(str(exc)) from exc
    previous = records.read_installed(ctx)
    if previous is not None and previous.get("version") == released.version:
        if records.status(ctx) is not None:
            raise errors.RecoveryRequiredError("complete payload recovery before installing")
        observed = control.status(ctx, include_rollback=False)
        runtime = observed.get("runtime")
        active = generation.selected_context(ctx)
        control_ctx = generation.control_context(ctx)
        if active is None or control_ctx is None:
            # An installed record without a selection names no payload that could be this one.
            raise errors.InstallError("requested artifact is not the verified active installation")
        configured = native_service.adapter().configured_executable(control_ctx)
        selected_settings = runtime_spec.environment(runtime_spec.path(control_ctx))
        if selected_settings[config.UPSTREAM_BASE_URL_ENV] != ctx.upstream_base_url:
            raise errors.InstallInputError(
                "upstream selection differs from the active installation"
            )
        if (
            observed.get("state") == "running"
            and isinstance(runtime, dict)
            and runtime.get("release_receipt_sha256") == released.receipt_sha256
            and runtime.get("serving_payload_sha256") == released.serving_payload_sha256
            and configured is not None
            and runtime_spec.normalized_path(configured)
            == runtime_spec.normalized_path(control_ctx.executable)
        ):
            return {"state": "unchanged", "release": released.version}
        if selected_settings[config.PROXY_PORT_ENV] == str(ctx.port) and _lacks_supervision(
            active, released, previous, observed
        ):
            command.project(Path(records.require_command(previous)), Path(control_ctx.executable))
            return {
                "state": "installed",
                "runtime": control.start_supervised(active, timeout_seconds=timeout_seconds),
            }
        raise errors.InstallError("requested artifact is not the verified active installation")
    payload_transaction = transaction.begin_transaction(ctx, released)
    with apply.closing_prepared(payload_transaction, "installation"):
        return apply.install(
            ctx,
            payload_transaction,
            adapter=native_service.adapter(),
            runtime_reader=control.read_runtime,
            timeout_seconds=timeout_seconds,
        )
