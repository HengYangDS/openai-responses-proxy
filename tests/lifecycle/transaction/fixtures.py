"""Transaction observations and recovery fixtures."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.transaction import execution
from openai_responses_proxy.lifecycle.transaction import recovery as payload_transaction
from openai_responses_proxy.serving import identity as listener_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import retained_rollback

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping

    from pytest_mock import MockerFixture


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


@dataclass(frozen=True)
class InterruptedTransition:
    """One transition that stopped short of its end, and what health may show of it."""

    transaction: execution.PayloadTransaction
    prior: payload_generation.Selection
    prior_context: runtime_context.RuntimeContext
    at_rest: dict[str, object]
    committed: dict[str, object] | None


def interrupted_transition(
    ctx: runtime_context.RuntimeContext,
    *,
    mocker: MockerFixture,
    reached: str = "activated",
    reverse: bool = False,
    preserved: bool = False,
) -> InterruptedTransition:
    """Stop the upgrade of 1.2.2 to 1.2.3, or the rollback from it, at one phase.

    ``at_rest`` is the health of the listener that the transition would replace, idle, and
    ``committed`` that of its successor once it serves the candidate.
    """
    serving = install_payload(ctx, "1.2.2", mocker=mocker)
    candidate = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    if reverse:
        candidate.commit_projection()
        candidate.activate()
        candidate.finalize({"pid": 2})
        serving = candidate
        candidate = execution.begin_rollback_transaction(ctx, retained_rollback(ctx))
    prior = payload_generation.read(ctx)
    assert prior is not None
    if reached != "prepared":
        candidate.commit_projection()
    if reached == "activated":
        candidate.activate()
    if preserved:
        candidate.preserve_for_recovery("handoff outcome is unconfirmed")
    serving_identity = listener_identity.committed_payload(Path(serving.context.executable))
    candidate_identity = listener_identity.committed_payload(Path(candidate.context.executable))
    assert serving_identity is not None
    return InterruptedTransition(
        transaction=candidate,
        prior=prior,
        prior_context=serving.context,
        at_rest=recovery_runtime(serving_identity),
        committed=(
            recovery_runtime(candidate_identity, candidate_identity)
            if candidate_identity is not None
            else None
        ),
    )


def observed_installation(ctx: runtime_context.InstallationContext) -> tuple[object, ...]:
    """Return the selection, the command's target, the journal and the installed record."""
    return (
        payload_generation.read(ctx),
        Path(ctx.command).resolve(strict=True),
        records.journal_path(ctx).read_bytes(),
        records.installed_path(ctx).read_bytes(),
    )


def no_wait() -> Mapping[str, object] | None:
    """Fail a recovery that waits for a handoff request where its test expects none."""
    raise AssertionError("recovery waited for a handoff request that may be pending")


def recover_transaction(
    ctx: runtime_context.InstallationContext,
    *,
    runtime: Mapping[str, object] | None,
    read_after_wait: Callable[[], Mapping[str, object] | None] = no_wait,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] = lambda _ctx: None,
    discard_native: Callable[[runtime_context.RuntimeContext], None] = lambda _ctx: None,
) -> dict[str, object]:
    """Exercise transaction recovery with an explicit terminal-binding boundary.

    A recovery that waits for a pending handoff request fails unless the test names what
    health shows after the wait.
    """
    return payload_transaction.recover(
        ctx,
        runtime=runtime,
        read_after_wait=read_after_wait,
        bind_terminal=bind_terminal,
        discard_native=discard_native,
    )


def _recover_without_binding(ctx: runtime_context.InstallationContext, outcome: str) -> None:
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
