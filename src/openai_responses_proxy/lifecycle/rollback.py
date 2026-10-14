"""Retained rollback authority derived from the sole generation selector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import identity


@dataclass(frozen=True, slots=True)
class RetainedRollback:
    """One verified predecessor bound to the installed successor generation."""

    root: Path
    predecessor: identity.LoadedPayloadIdentity
    successor: identity.LoadedPayloadIdentity


@dataclass(frozen=True, slots=True)
class RetainedRollbackStatus:
    """Secret-free availability of the one retained predecessor."""

    state: str
    from_release: str | None = None
    to_release: str | None = None
    detail: str | None = None


def remove_retained(ctx: runtime_context.RuntimeContext) -> None:
    """Drop the predecessor from the sole generation selector."""
    selection = generation.read(ctx)
    _require_closed_generation_store(ctx, selection)
    if selection is None or selection.predecessor is None:
        return
    generation.select(ctx, active=selection.active, predecessor=None)
    generation.prune(ctx, generation.Selection(selection.active, None))


def load_retained(ctx: runtime_context.RuntimeContext) -> RetainedRollback:
    """Load and verify the sole predecessor bound to the live successor."""
    selection = generation.read(ctx)
    _require_closed_generation_store(ctx, selection)
    if selection is None or selection.predecessor is None:
        raise errors.InstallError("retained rollback predecessor is unavailable")
    return _load_selected(ctx, selection)


def _load_selected(
    ctx: runtime_context.RuntimeContext,
    selection: generation.Selection,
) -> RetainedRollback:
    """Verify the identities bound by one already validated selection."""
    assert selection.predecessor is not None
    active_ctx = generation.context(ctx, selection.active)
    predecessor_ctx = generation.context(ctx, selection.predecessor)
    successor = identity.committed_payload(Path(active_ctx.executable))
    predecessor = identity.committed_payload(Path(predecessor_ctx.executable))
    installed = records.read_installed(ctx)
    if successor is None:
        raise errors.InstallError("retained rollback successor generation identity is invalid")
    if predecessor is None:
        raise errors.InstallError("retained rollback predecessor generation identity is invalid")
    if (
        installed is None
        or installed.get("transaction_id") != selection.active
        or installed.get("version") != successor.release
        or installed.get("receipt_sha256") != successor.release_receipt_sha256
        or installed.get("command") != ctx.command
    ):
        raise errors.InstallError("retained rollback installed binding is invalid")
    return RetainedRollback(
        Path(predecessor_ctx.payload_dir),
        predecessor,
        successor,
    )


def load_retained_or_none(
    ctx: runtime_context.RuntimeContext,
) -> RetainedRollback | None:
    """Return the retained predecessor, distinguishing clean absence from corruption."""
    selection = generation.read(ctx)
    _require_closed_generation_store(ctx, selection)
    if selection is None or selection.predecessor is None:
        return None
    return _load_selected(ctx, selection)


def _require_closed_generation_store(
    ctx: runtime_context.RuntimeContext,
    selection: generation.Selection | None,
) -> None:
    """Require the generation store to contain exactly the selected authority."""
    root = generation.root(ctx)
    if selection is None:
        if root.exists() or root.is_symlink():
            raise errors.InstallError("payload generation store exists without a durable selector")
        return
    if root.is_symlink() or not root.is_dir():
        raise errors.InstallError("payload generation store is unavailable or invalid")
    expected = {selection.active, selection.predecessor} - {None}
    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        raise errors.InstallError("payload generation store is unreadable") from exc
    if {entry.name for entry in entries} != expected or any(
        entry.is_symlink() or not entry.is_dir() for entry in entries
    ):
        raise errors.InstallError("payload generation store is not closed over its selector")


def status(ctx: runtime_context.RuntimeContext) -> RetainedRollbackStatus:
    """Inspect retained rollback availability without exposing carrier paths."""
    try:
        retained = load_retained_or_none(ctx)
    except errors.InstallError as exc:
        return RetainedRollbackStatus(state="invalid", detail=str(exc))
    if retained is None:
        return RetainedRollbackStatus(state="unavailable")
    return RetainedRollbackStatus(
        state="available",
        from_release=retained.successor.release,
        to_release=retained.predecessor.release,
    )
