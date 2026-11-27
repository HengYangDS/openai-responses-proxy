"""Immutable payload generations selected from one stable control root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openai_responses_proxy.lifecycle import context as runtime_context

SELECTOR_FILENAME = identity.PAYLOAD_SELECTOR_FILENAME
SELECTOR_SCHEMA = identity.PAYLOAD_SELECTOR_SCHEMA
GENERATIONS_DIRNAME = identity.PAYLOAD_GENERATIONS_DIRNAME
Selection = identity.PayloadSelection
# What every reader of a payload says of a control root without a selector.
NO_SELECTION = "no payload generation is selected"


def root(ctx: runtime_context.InstallationContext) -> Path:
    """Return the immutable payload-generation store."""
    return Path(ctx.install_dir, GENERATIONS_DIRNAME)


def path(ctx: runtime_context.InstallationContext, generation: str) -> Path:
    """Return one validated payload-generation directory."""
    _require_name(generation)
    return root(ctx) / generation


def selector_path(ctx: runtime_context.InstallationContext) -> Path:
    """Return the durable active-generation selector."""
    return Path(ctx.install_dir, SELECTOR_FILENAME)


def context(
    ctx: runtime_context.InstallationContext, generation: str
) -> runtime_context.RuntimeContext:
    """Project one immutable generation through the stable runtime settings."""
    return ctx.for_executable(inventory.installed_executable(str(path(ctx, generation))))


def read(ctx: runtime_context.InstallationContext) -> Selection | None:
    """Read and validate the selected generation without following links."""
    selector = selector_path(ctx)
    if not selector.exists() and not selector.is_symlink():
        return None
    if selector.is_symlink() or not selector.is_file():
        raise errors.InstallError("payload generation selector is invalid")
    try:
        selection = identity.read_payload_selection(selector)
    except ValueError:
        raise errors.InstallError("payload generation selector is invalid") from None

    for generation in (selection.active, selection.predecessor):
        if generation is None:
            continue
        selected = path(ctx, generation)
        if selected.is_symlink() or not selected.is_dir():
            raise errors.InstallError("selected payload generation is unavailable or invalid")
    return selection


def selected_context(
    ctx: runtime_context.InstallationContext,
) -> runtime_context.RuntimeContext | None:
    """Return the active generation's context, or nothing when no generation is selected."""
    selection = read(ctx)
    return context(ctx, selection.active) if selection is not None else None


def control_context(
    ctx: runtime_context.InstallationContext,
) -> runtime_context.RuntimeContext | None:
    """Return the newest selected generation, which owns installed lifecycle control.

    Without a selection no generation owns it, and nothing is returned.
    """
    selection = read(ctx)
    if selection is None:
        return None
    identified = []
    for generation_id in (selection.active, selection.predecessor):
        if generation_id is None:
            continue
        candidate = context(ctx, generation_id)
        payload = identity.committed_payload(Path(candidate.executable))
        if payload is None and identity.is_earlier_payload_shape(Path(candidate.executable)):
            raise errors.InstallError(
                "installed payload was written by an earlier release with another payload shape; "
                "remove it with that release's uninstall --purge, then install this release",
                next_command=product_identity.command("uninstall", "--purge"),
            )
        if payload is None:
            raise errors.InstallError("selected control-plane generation identity is invalid")
        identified.append((candidate, records.version_key(payload.release)))
    return max(identified, key=lambda item: item[1])[0]


def require_control_context(
    ctx: runtime_context.InstallationContext,
) -> runtime_context.RuntimeContext:
    """Return the control generation where the caller has read or written a selection.

    A selector that is gone by then was removed beside the running command, which stops.
    """
    control = control_context(ctx)
    if control is None:
        raise errors.InstallError(NO_SELECTION)
    return control


def owned_contexts(
    ctx: runtime_context.InstallationContext,
) -> tuple[runtime_context.RuntimeContext, ...]:
    """Return every exact immutable generation owned by the stable control root."""
    generations = root(ctx)
    if not generations.exists() and not generations.is_symlink():
        return ()
    if generations.is_symlink() or not generations.is_dir():
        raise errors.InstallError("payload generation store is unavailable or invalid")
    try:
        entries = tuple(sorted(generations.iterdir(), key=lambda entry: entry.name))
    except OSError as exc:
        raise errors.InstallError("payload generation store is unreadable") from exc
    contexts = []
    for entry in entries:
        _require_name(entry.name)
        if entry.is_symlink() or not entry.is_dir():
            raise errors.InstallError("payload generation store is invalid")
        contexts.append(context(ctx, entry.name))
    return tuple(contexts)


def select(
    ctx: runtime_context.InstallationContext,
    *,
    active: str,
    predecessor: str | None,
) -> None:
    """Atomically select one verified generation and at most one predecessor."""
    _require_generation(ctx, active, "active")
    if predecessor is not None:
        _require_generation(ctx, predecessor, "predecessor")
        if predecessor == active:
            raise errors.InstallError("payload generation selector is invalid")
    owned_files.write_bytes(
        selector_path(ctx),
        digest.canonical_json(
            {
                "schema_version": SELECTOR_SCHEMA,
                "active": active,
                "predecessor": predecessor,
            }
        ),
        mode=0o600,
        root=Path(ctx.install_dir),
    )


def clear(ctx: runtime_context.InstallationContext) -> None:
    """Remove only the owned selector."""
    selector = selector_path(ctx)
    if selector.is_symlink():
        raise errors.InstallError("payload generation selector is a symbolic link")
    try:
        selector.unlink(missing_ok=True)
    except OSError as exc:
        raise errors.InstallError("payload generation selector removal failed") from exc


def remove(
    ctx: runtime_context.InstallationContext, generation: str, files: Mapping[str, str]
) -> None:
    """Dispose only the recorded bytes of one unselected generation."""
    selection = read(ctx)
    if selection is not None and generation in {
        selection.active,
        selection.predecessor,
    }:
        raise errors.InstallError("selected payload generation cannot be removed")
    target = path(ctx, generation)
    generations = root(ctx)
    if generations.is_symlink() or (generations.exists() and not generations.is_dir()):
        raise errors.InstallError("payload generation store is unavailable or invalid")
    if target.is_symlink():
        raise errors.InstallError("payload generation is a symbolic link")
    if target.exists():
        if not target.is_dir():
            raise errors.InstallError("payload generation is invalid")
        try:
            remaining = projection.purge_owned_files(target, files)
        except OSError as exc:
            raise errors.InstallError(
                f"payload generation removal failed{errors.os_error_detail(exc)}"
            ) from exc
        if remaining:
            raise errors.InstallError("unknown generation content remains: " + ", ".join(remaining))
    try:
        if generations.is_dir() and not any(generations.iterdir()):
            generations.rmdir()
    except OSError as exc:
        raise errors.InstallError("empty payload generation store removal failed") from exc


def disposal_files(
    ctx: runtime_context.InstallationContext, selection: Selection
) -> dict[str, str]:
    """Capture verified retirement authority without including unowned content."""
    keep = {selection.active, selection.predecessor}
    return {
        f"{GENERATIONS_DIRNAME}/{Path(selected.payload_dir).name}/{relative}": expected
        for selected in owned_contexts(ctx)
        if Path(selected.payload_dir).name not in keep
        for relative, expected in projection.owned_payload_files(selected).items()
    }


def prune(
    ctx: runtime_context.InstallationContext, selection: Selection, files: Mapping[str, str]
) -> None:
    """Resume recorded retirement without reconstructing partially removed payloads."""
    generations = root(ctx)
    if generations.is_symlink() or not generations.is_dir():
        raise errors.InstallError("payload generation store is unavailable or invalid")
    keep = {selection.active, selection.predecessor} - {None}
    discarded: dict[str, dict[str, str]] = {}
    for relative, expected in files.items():
        root_name, _, rest = relative.partition("/")
        generation_name, separator, member = rest.partition("/")
        if not separator or root_name != GENERATIONS_DIRNAME or generation_name in keep:
            raise errors.InstallError("payload generation disposal inventory is invalid")
        _require_name(generation_name)
        discarded.setdefault(generation_name, {})[member] = expected
    for entry in generations.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            raise errors.InstallError("payload generation store is invalid")
        if entry.name not in keep and entry.name not in discarded:
            raise errors.InstallError("unknown generation content remains")
    for generation, members in sorted(discarded.items()):
        remove(ctx, generation, members)


def _require_generation(
    ctx: runtime_context.InstallationContext, generation: str, role: str
) -> None:
    selected = path(ctx, generation)
    if selected.is_symlink() or not selected.is_dir():
        raise errors.InstallError(f"{role} payload generation is unavailable or invalid")
    projected = context(ctx, generation)
    if identity.committed_payload(Path(projected.executable)) is None:
        raise errors.InstallError(f"{role} payload generation identity is invalid")


def _require_name(generation: str) -> None:
    try:
        identity.require_payload_generation_name(generation)
    except ValueError:
        raise errors.InstallError("payload generation identity is invalid") from None
