"""Read status or reload the installed OpenAI Responses Proxy payload."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import transaction
from openai_responses_proxy.lifecycle import uninstall
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.supervision.native_service import adapter
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime import loopback
from openai_responses_proxy.service import identity
from openai_responses_proxy.service import runtime as service_runtime


def read_runtime(ctx: runtime_context.RuntimeContext) -> dict[str, object] | None:
    """Read the proxy's secret-free health snapshot from loopback only."""
    request = urllib.request.Request(
        runtime_config.loopback_url(ctx.port, "/healthz"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with loopback.open_request(request, timeout_seconds=2) as response:
            if response.status != 200:
                return None
            payload: object = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        return None
    return {key: value for key, value in payload.items() if isinstance(key, str)}


def status(
    ctx: runtime_context.RuntimeContext,
    *,
    include_rollback: bool = True,
) -> dict[str, object]:
    """Return non-secret runtime and transaction evidence without mutation."""
    active = generation.selected_context(ctx)
    installed_error: str | None = None
    try:
        installed = payload_state.read_installed(ctx)
    except errors.InstallError as exc:
        installed = None
        installed_error = str(exc)
    try:
        integrity_ok, integrity_detail = projection.verify_payload_manifest(active)
    except errors.InstallError as exc:
        integrity_ok, integrity_detail = False, str(exc)
    try:
        service = adapter().status(active)
    except (OSError, errors.InstallError, errors.UnsupportedPlatformError):
        service = "unknown"
    listeners = process.verified_proxy_listener_pids(active)
    runtime = read_runtime(active)
    pid = runtime.get("pid") if isinstance(runtime, dict) else None
    runtime_process = (
        process.capture_executable(
            pid,
            active.executable,
            roles={service_runtime.LISTENER_MODE, service_runtime.HANDOFF_CHILD_MODE},
        )
        if type(pid) is int
        else None
    )
    committed = identity.committed_payload(Path(active.executable)) if integrity_ok else None
    selection = generation.read(ctx)
    installed_matches_payload = (
        installed is not None
        and committed is not None
        and installed.get("version") == committed.release
        and installed.get("receipt_sha256") == committed.release_receipt_sha256
        and installed.get("command") == ctx.command
        and (selection is None or installed.get("transaction_id") == selection.active)
    )
    runtime_matches_payload = (
        isinstance(runtime, dict)
        and committed is not None
        and identity.runtime_payload_matches(runtime, committed.handoff())
        and runtime.get("accepting") is True
        and runtime.get("draining") is False
    )
    if (
        type(pid) is not int
        or (listeners != [pid] and runtime_process is None)
        or not runtime_matches_payload
    ):
        runtime = None
    installed_command = (
        Path(payload_state.require_command(installed))
        if installed is not None
        else Path(active.command)
    )
    try:
        control_executable = generation.control_context(ctx).executable
    except errors.InstallError:
        control_executable = active.executable
    command_state = command.status(installed_command, Path(control_executable))
    payload_transaction = payload_state.status(ctx)
    rollback_status = (
        (
            payload_rollback.RetainedRollbackStatus(
                state="deferred",
                detail="payload transaction owns rollback finalization",
            )
            if payload_transaction is not None
            else payload_rollback.status(ctx)
        )
        if include_rollback
        else None
    )
    install_root = Path(ctx.install_dir)
    absent = (
        installed is None
        and not install_root.exists()
        and not install_root.is_symlink()
        and service == "absent"
        and not listeners
        and runtime is None
        and payload_transaction is None
        and command_state.get("state") == "absent"
    )
    transaction_invalid = (
        isinstance(payload_transaction, dict) and payload_transaction.get("state") == "invalid"
    )
    if installed_error is not None:
        lifecycle_state = "invalid"
        detail = installed_error
    elif transaction_invalid:
        assert isinstance(payload_transaction, dict)
        lifecycle_state = "invalid"
        detail = str(payload_transaction["detail"])
    elif rollback_status is not None and rollback_status.state == "invalid":
        lifecycle_state = "invalid"
        detail = rollback_status.detail or "selected rollback predecessor is invalid"
    elif absent:
        lifecycle_state = "not_installed"
        integrity_detail = "not installed"
        detail = "not installed"
    elif (
        integrity_ok
        and installed_matches_payload
        and service == "running"
        and runtime is not None
        and command_state.get("state") == "owned"
        and payload_transaction is None
    ):
        lifecycle_state = "running"
        detail = "healthy"
    elif (
        isinstance(payload_transaction, dict)
        and payload_transaction.get("state")
        in {
            "prepared",
            "materialized",
            "activated",
            "recovery_required",
        }
        | payload_state.TERMINAL_STATES
    ):
        lifecycle_state = "recovery_required"
        detail = (
            "payload cleanup is required"
            if payload_transaction["state"] in payload_state.TERMINAL_STATES
            else "payload recovery is required"
        )
    else:
        lifecycle_state = "degraded"
        if not integrity_ok:
            detail = integrity_detail
        elif installed is not None and not installed_matches_payload:
            detail = "installed release state does not match selected payload"
        elif service != "running":
            detail = f"native service is {service}"
        elif runtime is None:
            detail = "listener runtime identity is unavailable"
        elif command_state.get("state") != "owned":
            detail = "native command ownership is unavailable"
        else:
            detail = "installation is degraded"
    evidence: dict[str, object] = {
        "state": lifecycle_state,
        "detail": detail,
        "release": payload_state.require_version(installed) if installed is not None else None,
        "command": command_state,
        "payload_integrity": {"ok": integrity_ok, "detail": integrity_detail},
        "service": service,
        "listener_pids": listeners,
        "runtime": runtime,
        "payload_transaction": payload_transaction,
    }
    if rollback_status is not None:
        evidence["rollback"] = {
            key: value
            for key, value in {
                "state": rollback_status.state,
                "from_release": rollback_status.from_release,
                "to_release": rollback_status.to_release,
                "detail": rollback_status.detail,
            }.items()
            if value is not None
        }
    return evidence


def reload(ctx: runtime_context.RuntimeContext, timeout_seconds: float = 30.0) -> dict[str, object]:
    """Reload the same installed protocol-v2 payload through live handoff."""
    installed = payload_state.read_installed(ctx)
    install_root = Path(ctx.install_dir)
    if installed is None and not install_root.exists() and not install_root.is_symlink():
        raise errors.NotInstalledError
    transaction_state = payload_state.status(ctx)
    if transaction_state is not None:
        if transaction_state.get("state") == "invalid":
            raise errors.RecoveryStateError(
                "payload transaction evidence is invalid; preserve it for diagnosis"
            )
        raise errors.RecoveryRequiredError("complete payload recovery before reloading")
    runtime = read_runtime(ctx)
    if not handoff.runtime_supports_handoff(runtime):
        raise errors.InstallError("installed runtime is not healthy enough to reload")
    assert runtime is not None
    integrity_ok, integrity_detail = projection.verify_payload_manifest(ctx)
    if not integrity_ok:
        raise errors.InstallError(
            f"payload integrity check failed before handoff reload: {integrity_detail}"
        )
    expected = handoff.expected_metadata(ctx.payload_dir)
    lease_seconds = max(1.0, timeout_seconds)
    source_listener = handoff.capture_source_listener(ctx, runtime)
    try:
        result = handoff.request(
            ctx,
            expected,
            runtime_reader=read_runtime,
            timeout_seconds=timeout_seconds,
            lease_seconds=lease_seconds,
            source_listener=source_listener,
            source_runtime=runtime,
        )
    except BaseException as handoff_exc:
        try:
            resolution, resolved_runtime = handoff.resolve_after_controller_failure(
                ctx,
                runtime,
                expected,
                runtime_reader=read_runtime,
                timeout_seconds=timeout_seconds,
                lease_seconds=lease_seconds,
                source_listener=source_listener,
            )
        except (OSError, RuntimeError):
            resolution, resolved_runtime = "unknown", None
        if resolution == "finalized" and isinstance(resolved_runtime, dict):
            return {
                "state": "reloaded",
                "old_pid": runtime["pid"],
                "new_pid": resolved_runtime["pid"],
                "transaction_id": expected["transaction_id"],
                "recovered_after_controller_failure": True,
            }
        if resolution == "unknown":
            raise errors.InstallError(
                "reload handoff outcome is unconfirmed; inspect transaction-bound listener health"
            ) from handoff_exc
        raise
    return {
        "state": "reloaded",
        "old_pid": result["old_pid"],
        "new_pid": result["child_pid"],
    }


def recover(ctx: runtime_context.RuntimeContext) -> dict[str, object]:
    """Converge one interrupted payload transaction and its native supervisor."""
    if (pending := payload_state.status(ctx)) is not None and pending.get("state") == "purged":
        return uninstall.uninstall_product(ctx, purge=True)

    def bind_terminal(control: runtime_context.RuntimeContext) -> None:
        native = adapter()
        native.install(control)
        configured = native.configured_executable(control)
        if configured is None or runtime_spec.normalized_path(
            configured
        ) != runtime_spec.normalized_path(control.executable):
            raise errors.RecoveryStateError(
                "payload recovery cannot close before native supervisor identity is proved"
            )

    def discard_native(candidate: runtime_context.RuntimeContext) -> None:
        apply.discard_runtime(candidate, adapter=adapter(), timeout_seconds=5.0)

    return transaction.recover(
        ctx,
        runtime=read_runtime(ctx),
        bind_terminal=bind_terminal,
        discard_native=discard_native,
    )


def rollback(
    ctx: runtime_context.RuntimeContext,
    *,
    to_release: str,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Converge on one explicit installed or retained release."""
    try:
        payload_state.version_key(to_release)
    except errors.InstallError as exc:
        raise errors.RollbackStateError("requested rollback release is invalid") from exc
    if payload_state.status(ctx) is not None:
        raise errors.RecoveryRequiredError("complete payload recovery before rollback")
    try:
        installed = payload_state.read_installed(ctx)
        active = generation.selected_context(ctx) if installed is not None else None
        active_identity = (
            identity.committed_payload(Path(active.executable)) if active is not None else None
        )
        selection = generation.read(ctx) if installed is not None else None
    except errors.InstallError as exc:
        raise errors.RollbackStateError(str(exc)) from exc
    if installed is not None and payload_state.require_version(installed) == to_release:
        observed = status(ctx, include_rollback=False)
        if not (
            observed.get("state") == "running"
            and observed.get("release") == to_release
            and active_identity is not None
            and active_identity.release == to_release
            and installed.get("receipt_sha256") == active_identity.release_receipt_sha256
            and (selection is None or installed.get("transaction_id") == selection.active)
        ):
            raise errors.RollbackStateError(
                "requested rollback release is installed but not proven active"
            )
        return {"state": "unchanged", "release": to_release}
    try:
        retained = payload_rollback.load_retained_or_none(ctx)
    except errors.InstallError as exc:
        raise errors.RollbackStateError(str(exc)) from exc
    if retained is None:
        return {"state": "unavailable", "detail": "no verified predecessor is retained"}
    if retained.predecessor.release != to_release:
        raise errors.RollbackStateError(
            f"requested release {to_release} is not the verified predecessor"
        )
    return apply.rollback(
        ctx,
        retained,
        adapter=adapter(),
        runtime_reader=read_runtime,
        timeout_seconds=timeout_seconds,
    )
