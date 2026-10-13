"""Transaction observations and owned historical-payload fixtures."""

from __future__ import annotations

import os
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import transaction as payload_transaction
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import executable_relative


def recovery_runtime(
    runtime_identity: listener_identity.LoadedPayloadIdentity,
    candidate_identity: listener_identity.LoadedPayloadIdentity | None = None,
) -> dict[str, object]:
    """Project one accepting runtime for one exact committed payload."""
    return {
        "pid": 321,
        "release": runtime_identity.release,
        "serving_payload_sha256": runtime_identity.serving_payload_sha256,
        "release_receipt_sha256": runtime_identity.release_receipt_sha256,
        "payload_manifest_sha256": (candidate_identity or runtime_identity).manifest_sha256,
        "accepting": True,
        "draining": False,
        "handoff_state": "idle",
    }


def recover_transaction(
    ctx: runtime_context.RuntimeContext,
    *,
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] = lambda _ctx: None,
    discard_native: Callable[[runtime_context.RuntimeContext], None] = lambda _ctx: None,
) -> dict[str, object]:
    """Exercise transaction recovery with an explicit terminal-binding boundary."""
    return payload_transaction.recover(
        ctx,
        runtime=runtime,
        bind_terminal=bind_terminal,
        discard_native=discard_native,
    )


def _recover_without_binding(ctx: runtime_context.RuntimeContext, outcome: str) -> None:
    """Exercise restart recovery in a fresh interpreter without native effects."""

    def unexpected_binding(_ctx: runtime_context.RuntimeContext) -> None:
        raise AssertionError("terminal cleanup repeated service binding")

    result = recover_transaction(ctx, runtime=None, bind_terminal=unexpected_binding)
    assert result["state"] == outcome


def _retained_carrier_snapshot(root: Path, external_target: Path) -> tuple[object, ...]:
    """Capture exact test-owned carrier shape and bytes without following links."""
    if root.is_symlink():
        return (
            "root-link",
            os.readlink(root),
            (external_target / "evidence").read_bytes(),
        )
    if root.is_file():
        return ("root-file", root.read_bytes())
    entries: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "link", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "directory"))
        else:
            entries.append((relative, "file", path.read_bytes()))
    target = external_target.read_bytes() if external_target.is_file() else None
    return ("root-directory", tuple(entries), target)


def _project_as_legacy_flat_install(ctx, installed) -> None:
    """Recreate a manifest-owned flat payload for explicit removal checks."""
    generation_root = Path(installed.context.payload_dir)
    install_root = Path(ctx.install_dir)
    for child in tuple(generation_root.iterdir()):
        child.rename(install_root / child.name)
    payload_generation.clear(ctx)
    generation_root.rmdir()
    ctx.executable = str(install_root / executable_relative())
    command_path = Path(ctx.command)
    command_path.unlink()
    command_path.symlink_to(ctx.executable)
