"""Persist transaction terminal effects before disposing exact owned inputs.

One owner selects retained generations, restores prior projections, records
terminal authority and resumes cleanup without replaying completed effects.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity


def close_finalized(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] | None,
) -> dict[str, object]:
    """Remove transaction residue only after finalized state proves the candidate."""
    _select_retention(ctx, journal=journal)
    if bind_terminal is not None:
        bind_terminal(generation.control_context(ctx))
    return complete_transaction(ctx, journal, outcome="finalized")


def finalize_installation(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    runtime: Mapping[str, object],
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] | None,
) -> dict[str, object]:
    """Record one serving installation and close its retained transaction."""
    installed = {
        "schema_version": records.INSTALLED_RELEASE_STATE_SCHEMA,
        "version": journal["version"],
        "receipt_sha256": journal["receipt_sha256"],
        "transaction_id": journal["transaction_id"],
        "command": ctx.command,
        "runtime": dict(runtime),
    }
    owned_files.write_bytes(
        records.installed_path(ctx),
        digest.canonical_json(installed),
        mode=0o600,
    )
    return close_finalized(ctx, journal=journal, bind_terminal=bind_terminal)


def _select_retention(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
) -> generation.Selection:
    """Select final retained generations without pruning recovery material."""
    try:
        active = str(journal["transaction_id"])
        previous_generation = journal.get("previous_generation")
        if journal["fresh"] is True or previous_generation is None:
            selection = generation.Selection(active, None)
        else:
            previous = str(previous_generation)
            if not generation.path(ctx, previous).is_dir():
                raise errors.InstallError("predecessor payload generation is unavailable")
            selection = generation.Selection(active, previous)
        generation.select(ctx, active=selection.active, predecessor=selection.predecessor)
        return selection
    except errors.InstallError:
        records.write_journal(
            ctx,
            transaction_id=str(journal["transaction_id"]),
            version=str(journal["version"]),
            receipt_sha256=str(journal["receipt_sha256"]),
            state="recovery_required",
            fresh=bool(journal["fresh"]),
            previous_generation=(
                str(journal["previous_generation"])
                if journal.get("previous_generation") is not None
                else None
            ),
            previous_predecessor=(
                str(journal["previous_predecessor"])
                if journal.get("previous_predecessor") is not None
                else None
            ),
            phase="activated",
            reason="finalization failed",
            files=records.removal_files(journal),
            snapshot_sha256=str(journal["snapshot_sha256"]),
        )
        raise


def prior_selection(journal: Mapping[str, object]) -> generation.Selection | None:
    """Decode the exact generation pair owned before this transaction."""
    previous = journal.get("previous_generation")
    predecessor = journal.get("previous_predecessor")
    return (
        generation.Selection(str(previous), str(predecessor) if predecessor is not None else None)
        if previous is not None
        else None
    )


def admit_prior_projection(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
    previous: generation.Selection | None,
) -> tuple[generation.Selection | None, command.Snapshot]:
    """Prove selector and command restoration before any native or file effect."""
    selection = generation.read(ctx)
    candidate = str(journal["transaction_id"])
    selected_candidate = generation.Selection(candidate, previous.active if previous else None)
    if selection not in {previous, selected_candidate}:
        raise errors.RecoveryStateError("payload transaction selection changed")
    snapshot = command.read_snapshot(records.transaction_root(ctx) / "rollback")
    if selection != previous:
        command.require_detachable(
            Path(ctx.command), Path(generation.context(ctx, candidate).executable), snapshot
        )
    else:
        command.require_restorable(
            Path(ctx.command), Path(generation.control_context(ctx).executable), snapshot
        )
    return selection, snapshot


def restore_prior_projection(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
    previous: generation.Selection | None,
) -> None:
    """Resume exact selector and command restoration as independent durable effects."""
    selection, snapshot = admit_prior_projection(ctx, journal, previous)
    candidate = str(journal["transaction_id"])
    if selection != previous:
        command.detach(
            Path(ctx.command), Path(generation.context(ctx, candidate).executable), snapshot
        )
        if previous is None:
            generation.clear(ctx)
        else:
            generation.select(ctx, active=previous.active, predecessor=previous.predecessor)
    command.restore(Path(ctx.command), Path(generation.control_context(ctx).executable), snapshot)


def reuses_retained_generation(journal: Mapping[str, object]) -> bool:
    """Return whether a reverse transition targets the selected predecessor itself."""
    return journal.get("transaction_id") == journal.get("previous_predecessor")


def complete_transaction(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
    *,
    outcome: Literal["closed", "rolled_back", "finalized"],
) -> dict[str, object]:
    """Persist terminal effects before deleting any recovery input."""
    terminal = {key: value for key, value in journal.items() if key not in {"phase", "reason"}}
    terminal["state"] = outcome
    if outcome != "rolled_back":
        terminal.pop("files", None)
    owned_files.write_bytes(records.journal_path(ctx), digest.canonical_json(terminal), mode=0o600)
    return finish_cleanup(ctx, terminal)


def purge(ctx: runtime_context.RuntimeContext) -> dict[str, object]:
    """Record exact disposal authority after supervision and processes have exited."""
    if records.status(ctx) is not None:
        raise errors.RecoveryRequiredError("complete payload recovery before uninstalling")
    root = Path(ctx.install_dir)
    files: JsonObject = {}
    for selected in generation.owned_contexts(ctx) or (ctx,):
        payload_root = Path(selected.payload_dir)
        for relative, expected in projection.owned_payload_files(selected).items():
            files[(payload_root / relative).relative_to(root).as_posix()] = expected
    installed = records.read_installed(ctx)
    if installed is None:
        manifest = owned_files.read_json_object(
            projection.payload_manifest_path(ctx), "installed payload manifest"
        )
        version = manifest["release"]
        receipt_sha256 = manifest["release_receipt_sha256"]
    else:
        version = records.require_version(installed)
        receipt_sha256 = installed["receipt_sha256"]
    for target in (generation.selector_path(ctx), records.installed_path(ctx)):
        if target.exists() or target.is_symlink():
            relative = target.relative_to(root).as_posix()
            files[relative] = digest.sha256_file(
                owned_files.regular_file(root, relative, "payload removal")
            )
    journal: JsonObject = {
        "schema_version": records.TRANSACTION_JOURNAL_SCHEMA,
        "transaction_id": uuid.uuid4().hex,
        "version": version,
        "receipt_sha256": receipt_sha256,
        "fresh": False,
        "state": "purged",
        "files": files,
    }
    records.removal_files(journal)
    owned_files.write_bytes(records.journal_path(ctx), digest.canonical_json(journal), mode=0o600)
    return finish_cleanup(ctx, journal)


def finish_cleanup(
    ctx: runtime_context.RuntimeContext, journal: Mapping[str, object]
) -> dict[str, object]:
    """Resume only disposal; terminal projections and supervision remain unchanged."""
    match journal["state"]:
        case "purged":
            files = records.removal_files(journal)
            root = Path(ctx.install_dir)
            # Detach selection before any generation can become partially absent.
            selector = identity.PAYLOAD_SELECTOR_FILENAME
            if selector in files:
                projection.purge_owned_files(root, {selector: files[selector]})
            remaining = projection.purge_owned_files(root, files)
            if remaining:
                raise errors.InstallError(
                    "unknown install content remains: " + ", ".join(remaining)
                )
        case "finalized":
            selection = generation.read(ctx)
            if selection is None or selection.active != journal["transaction_id"]:
                raise errors.RecoveryStateError("finalized transaction selection changed")
            if "files" not in journal:
                journal = {**journal, "files": generation.disposal_files(ctx, selection)}
                owned_files.write_bytes(
                    records.journal_path(ctx), digest.canonical_json(journal), mode=0o600
                )
            generation.prune(ctx, selection, records.removal_files(journal))
        case "rolled_back":
            if not reuses_retained_generation(journal):
                candidate = str(journal["transaction_id"])
                prefix = f"{generation.GENERATIONS_DIRNAME}/{candidate}/"
                generation.remove(
                    ctx,
                    candidate,
                    {
                        name.removeprefix(prefix): expected
                        for name, expected in records.removal_files(journal).items()
                    },
                )
            install = Path(ctx.install_dir)
            if journal["fresh"] is True and install.is_dir() and not any(install.iterdir()):
                install.rmdir()
        case "closed":
            pass
        case _:
            raise errors.RecoveryStateError("payload transaction is not terminal")
    _remove_transaction_root(ctx, snapshot_sha256=journal.get("snapshot_sha256"))
    try:
        records.journal_path(ctx).unlink()
    except OSError as exc:
        raise errors.InstallError("payload transaction cleanup failed") from exc
    return {
        "transaction_id": journal["transaction_id"],
        "version": journal["version"],
        "state": journal["state"],
    }


def _remove_transaction_root(
    ctx: runtime_context.RuntimeContext, *, snapshot_sha256: object = None
) -> None:
    root = records.transaction_root(ctx)
    if not root.exists() and not root.is_symlink():
        return
    try:
        if root.is_symlink() or not root.is_dir():
            raise errors.InstallError("payload transaction root is not a real directory")
        relative = f"rollback/{command.SNAPSHOT_FILENAME}"
        files = {relative: str(snapshot_sha256)} if snapshot_sha256 is not None else {}
        projection.remove_empty_owned_directories(root, {relative})
        remaining = projection.purge_owned_files(root, files)
    except (OSError, errors.InstallError) as exc:
        raise errors.InstallError("payload transaction cleanup failed") from exc
    if remaining:
        raise errors.InstallError("unknown transaction content remains: " + ", ".join(remaining))
    if root.exists():
        raise errors.InstallError("payload transaction cleanup did not remove the transaction")
