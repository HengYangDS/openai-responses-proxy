"""Reconcile an interrupted transaction with exact serving and journal evidence.

Recovery proves the terminal generation before delegating its durable effects
and disposal. It never reconstructs a live controller capability.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import completion
from openai_responses_proxy.serving import identity


def recover(
    ctx: runtime_context.RuntimeContext,
    *,
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
    discard_native: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Recover one transaction and bind its terminal runtime before closing it."""
    if records.status(ctx) is None:
        return {"state": "not_required"}
    try:
        journal = records.read_journal(ctx)
    except errors.InstallError as exc:
        raise errors.RecoveryStateError(str(exc)) from exc
    if journal["state"] in records.TERMINAL_STATES:
        return completion.finish_cleanup(ctx, journal)
    phase = str(journal.get("phase") or journal["state"])
    if phase == "prepared":
        return _close_prepared(ctx, journal)
    candidate = _recovery_candidate(ctx, journal)
    if phase == "materialized" and not _runtime_matches_projection(runtime, candidate):
        return _rollback_materialized(
            ctx,
            journal=journal,
            runtime=runtime,
            bind_terminal=bind_terminal,
            discard_native=discard_native,
        )
    installed = records.read_installed(ctx)
    if installed is not None and _installed_matches_transaction(
        installed,
        journal=journal,
        candidate=candidate,
        command_path=ctx.command,
    ):
        return completion.close_finalized(
            ctx,
            journal=journal,
            bind_terminal=bind_terminal,
        )
    if runtime is not None and _runtime_matches_projection(runtime, candidate):
        if phase == "materialized":
            rollback = records.transaction_root(ctx) / "rollback"
            generation.select(
                ctx,
                active=str(journal["transaction_id"]),
                predecessor=(
                    str(journal["previous_generation"])
                    if journal.get("previous_generation") is not None
                    and generation.path(ctx, str(journal["previous_generation"])).is_dir()
                    else None
                ),
            )
            command.project(
                Path(ctx.command),
                Path(generation.control_context(ctx).executable),
                previous=command.read_snapshot(rollback),
            )
        return completion.finalize_installation(
            ctx,
            journal=journal,
            runtime=runtime,
            bind_terminal=bind_terminal,
        )
    if journal["fresh"] is True:
        if runtime is not None:
            raise errors.InstallError(
                "payload recovery runtime does not match the candidate projection"
            )
        return _rollback_materialized(
            ctx,
            journal=journal,
            runtime=runtime,
            bind_terminal=bind_terminal,
            discard_native=discard_native,
        )
    return _restore_prior_generation(
        ctx,
        journal=journal,
        runtime=runtime,
        bind_terminal=bind_terminal,
    )


def _close_prepared(
    ctx: runtime_context.RuntimeContext, journal: Mapping[str, object]
) -> dict[str, object]:
    """Remove only a valid journal that proves no payload mutation began."""
    root = records.transaction_root(ctx)
    if any(root.iterdir()):
        raise errors.RecoveryStateError("prepared transaction contains unowned content")
    return completion.complete_transaction(ctx, journal, outcome="closed")


def _recovery_candidate(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
) -> identity.LoadedPayloadIdentity:
    """Verify one mutated transaction and its exact candidate projection."""
    candidate_ctx = generation.context(ctx, str(journal["transaction_id"]))
    candidate = identity.committed_payload(Path(candidate_ctx.executable))
    if candidate is None:
        raise errors.RecoveryStateError("payload recovery candidate projection identity is invalid")
    if candidate.release != journal["version"] or candidate.release_receipt_sha256 != journal.get(
        "receipt_sha256"
    ):
        raise errors.RecoveryStateError("payload recovery candidate does not match the transaction")
    return candidate


def _installed_matches_transaction(
    installed: Mapping[str, object],
    *,
    journal: Mapping[str, object],
    candidate: identity.LoadedPayloadIdentity,
    command_path: str,
) -> bool:
    """Return whether finalized state proves this exact candidate transaction."""
    return (
        installed.get("transaction_id") == journal["transaction_id"]
        and installed.get("version") == candidate.release
        and installed.get("receipt_sha256") == candidate.release_receipt_sha256
        and installed.get("command") == command_path
    )


def _runtime_matches_projection(
    runtime: Mapping[str, object] | None,
    projection: identity.LoadedPayloadIdentity,
) -> bool:
    """Return whether one accepting runtime serves the verified projection."""
    return (
        runtime is not None
        and identity.runtime_payload_matches(runtime, projection.handoff())
        and runtime.get("accepting") is True
        and runtime.get("draining") is False
        and runtime.get("handoff_state") in {"idle", "serving", "finalized"}
    )


def _rollback_materialized(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
    discard_native: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Discard a candidate that never became the proven serving runtime."""
    previous = completion.prior_selection(journal)
    selection = generation.read(ctx)
    if completion.reuses_retained_generation(journal):
        if selection == previous and not (records.transaction_root(ctx) / "rollback").exists():
            assert previous is not None
            _require_unchanged_prior_terminal(ctx, generation_id=previous.active, runtime=runtime)
            return completion.complete_transaction(ctx, journal, outcome="rolled_back")
        return _restore_prior_generation(
            ctx, journal=journal, runtime=runtime, bind_terminal=bind_terminal
        )
    if previous is None:
        completion.admit_prior_projection(ctx, journal, previous)
        # A missing health response does not prove no registered or starting
        # native service. Stop it while journal and payload still prove ownership.
        discard_native(generation.context(ctx, str(journal["transaction_id"])))
    completion.restore_prior_projection(ctx, journal, previous)
    if previous is not None:
        bind_terminal(generation.control_context(ctx))
    return completion.complete_transaction(ctx, journal, outcome="rolled_back")


def _require_unchanged_prior_terminal(
    ctx: runtime_context.RuntimeContext,
    *,
    generation_id: str,
    runtime: Mapping[str, object] | None,
) -> None:
    """Prove the unselected reverse candidate changed no terminal authority."""
    prior_ctx = generation.context(ctx, generation_id)
    prior = identity.committed_payload(Path(prior_ctx.executable))
    if prior is None:
        raise errors.RecoveryStateError(
            "payload recovery prior selected generation identity is invalid"
        )
    try:
        installed = records.read_installed(ctx)
        installed_command = records.require_command(installed) if installed is not None else None
    except errors.InstallError as exc:
        raise errors.RecoveryStateError(str(exc)) from exc
    if installed is None or not (
        installed.get("transaction_id") == generation_id
        and installed.get("version") == prior.release
        and installed.get("receipt_sha256") == prior.release_receipt_sha256
    ):
        raise errors.RecoveryStateError(
            "payload recovery installed state does not match the prior selected generation"
        )
    if (
        installed_command != ctx.command
        or command.status(Path(installed_command), Path(prior_ctx.executable)).get("state")
        != "owned"
    ):
        raise errors.RecoveryStateError(
            "payload recovery command does not match the prior selected generation"
        )
    if not _runtime_matches_projection(runtime, prior):
        raise errors.RecoveryStateError(
            "payload recovery runtime does not match the prior selected generation"
        )


def _restore_prior_generation(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Restore the exact prior projection and bind its proven live runtime."""
    previous = completion.prior_selection(journal)
    if previous is None or not generation.path(ctx, previous.active).is_dir():
        raise errors.RecoveryStateError("payload recovery prior selected generation is unavailable")
    previous_ctx = generation.context(ctx, previous.active)
    identity_before = identity.committed_payload(Path(previous_ctx.executable))
    if identity_before is None or not _runtime_matches_projection(runtime, identity_before):
        raise errors.RecoveryStateError(
            "payload recovery runtime does not match the prior selected generation"
        )
    completion.restore_prior_projection(ctx, journal, previous)
    bind_terminal(generation.control_context(ctx))
    return completion.complete_transaction(ctx, journal, outcome="rolled_back")
