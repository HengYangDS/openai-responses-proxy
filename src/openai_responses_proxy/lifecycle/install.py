"""Verify and transactionally install one signed native release asset."""

from __future__ import annotations

from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle import state
from openai_responses_proxy.lifecycle import transaction
from openai_responses_proxy.lifecycle.deployment import apply


def install_asset(
    ctx: runtime_context.RuntimeContext,
    asset: Path,
    *,
    trust_anchor: Path,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Install one verified native asset through the platform service adapter."""
    from openai_responses_proxy.lifecycle.supervision import native_service

    try:
        released = artifact.admit(asset, trust_anchor=trust_anchor)
    except errors.InstallError as exc:
        raise errors.InstallInputError(str(exc)) from exc
    previous = state.read_installed(ctx)
    if previous is not None and previous.get("version") == released.version:
        if state.status(ctx) is not None:
            raise errors.RecoveryRequiredError("complete payload recovery before installing")
        observed = control.status(ctx, include_rollback=False)
        runtime = observed.get("runtime")
        control_ctx = generation.control_context(ctx)
        configured = native_service.adapter().configured_executable(control_ctx)
        selected_settings = runtime_spec.environment(runtime_spec.path(control_ctx))
        from openai_responses_proxy.runtime import config

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
        raise errors.InstallError("requested artifact is not the verified active installation")
    payload_transaction = transaction.begin_transaction(ctx, released)
    try:
        return apply.install(
            ctx,
            payload_transaction,
            adapter=native_service.adapter(),
            runtime_reader=control.read_runtime,
            timeout_seconds=timeout_seconds,
        )
    except BaseException as install_error:
        try:
            payload_transaction.rollback_if_prepared()
        except BaseException as cleanup_error:
            message = (
                f"installation failed: {errors.failure_summary(install_error)}; "
                f"prepared payload cleanup failed: {errors.failure_summary(cleanup_error)}"
            )
            failure = (
                type(install_error)(message)
                if isinstance(install_error, errors.InstallError)
                else errors.InstallError(message)
            )
            if isinstance(install_error, errors.ProductError):
                failure.code = install_error.code
                failure.next_command = install_error.next_command
            raise failure from cleanup_error
        raise
