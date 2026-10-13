"""Source-side state machine for one admitted payload transaction.

The transaction consumes one admitted payload capability and coordinates the
candidate, rollback, projection, and journal owners. It owns only
state transitions and transaction-root cleanup; it does not reimplement those
collaborators or own installed manifest reads and purge.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import candidate as payload_candidate
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle import state
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity


def recover(
    ctx: runtime_context.RuntimeContext,
    *,
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
    discard_native: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Recover one transaction and bind its terminal runtime before closing it."""
    if state.status(ctx) is None:
        return {"state": "not_required"}
    try:
        journal = state.read_journal(ctx)
    except errors.InstallError as exc:
        raise errors.RecoveryStateError(str(exc)) from exc
    if journal["state"] in state.TERMINAL_STATES:
        return _finish_cleanup(ctx, journal)
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
    installed = state.read_installed(ctx)
    if installed is not None and _installed_matches_transaction(
        installed,
        journal=journal,
        candidate=candidate,
        command_path=ctx.command,
    ):
        return _close_finalized(
            ctx,
            journal=journal,
            bind_terminal=bind_terminal,
        )
    if runtime is not None and _runtime_matches_projection(runtime, candidate):
        if phase == "materialized":
            rollback = state.transaction_root(ctx) / "rollback"
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
        return _finalize_installation(
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
    root = state.transaction_root(ctx)
    if any(root.iterdir()):
        raise errors.RecoveryStateError("prepared transaction contains unowned content")
    return _complete_transaction(ctx, journal, outcome="closed")


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


def _close_finalized(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] | None,
) -> dict[str, object]:
    """Remove transaction residue only after finalized state proves the candidate."""
    _select_retention(ctx, journal=journal)
    if bind_terminal is not None:
        bind_terminal(generation.control_context(ctx))
    return _complete_transaction(ctx, journal, outcome="finalized")


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


def _finalize_installation(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    runtime: Mapping[str, object],
    bind_terminal: Callable[[runtime_context.RuntimeContext], None] | None,
) -> dict[str, object]:
    """Record one serving installation and close its retained transaction."""
    installed = {
        "schema_version": state.INSTALLED_RELEASE_STATE_SCHEMA,
        "version": journal["version"],
        "receipt_sha256": journal["receipt_sha256"],
        "transaction_id": journal["transaction_id"],
        "command": ctx.command,
        "runtime": dict(runtime),
    }
    owned_files.write_bytes(
        state.installed_path(ctx),
        digest.canonical_json(installed),
        mode=0o600,
    )
    return _close_finalized(ctx, journal=journal, bind_terminal=bind_terminal)


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
        state.write_journal(
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
        )
        raise


def _prior_selection(journal: Mapping[str, object]) -> generation.Selection | None:
    """Decode the exact generation pair owned before this transaction."""
    previous = journal.get("previous_generation")
    predecessor = journal.get("previous_predecessor")
    return (
        generation.Selection(str(previous), str(predecessor) if predecessor is not None else None)
        if previous is not None
        else None
    )


def _admit_prior_projection(
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
    snapshot = command.read_snapshot(state.transaction_root(ctx) / "rollback")
    if selection != previous:
        command.require_detachable(
            Path(ctx.command), Path(generation.context(ctx, candidate).executable), snapshot
        )
    else:
        command.require_restorable(
            Path(ctx.command), Path(generation.control_context(ctx).executable), snapshot
        )
    return selection, snapshot


def _restore_prior_projection(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
    previous: generation.Selection | None,
) -> None:
    """Resume exact selector and command restoration as independent durable effects."""
    selection, snapshot = _admit_prior_projection(ctx, journal, previous)
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


def _rollback_materialized(
    ctx: runtime_context.RuntimeContext,
    *,
    journal: Mapping[str, object],
    runtime: Mapping[str, object] | None,
    bind_terminal: Callable[[runtime_context.RuntimeContext], None],
    discard_native: Callable[[runtime_context.RuntimeContext], None],
) -> dict[str, object]:
    """Discard a candidate that never became the proven serving runtime."""
    previous = _prior_selection(journal)
    selection = generation.read(ctx)
    if _reuses_retained_generation(journal):
        if selection == previous and not (state.transaction_root(ctx) / "rollback").exists():
            assert previous is not None
            _require_unchanged_prior_terminal(ctx, generation_id=previous.active, runtime=runtime)
            return _complete_transaction(ctx, journal, outcome="rolled_back")
        return _restore_prior_generation(
            ctx, journal=journal, runtime=runtime, bind_terminal=bind_terminal
        )
    if previous is None:
        _admit_prior_projection(ctx, journal, previous)
        # A missing health response does not prove no registered or starting
        # native service. Stop it while journal and payload still prove ownership.
        discard_native(generation.context(ctx, str(journal["transaction_id"])))
    _restore_prior_projection(ctx, journal, previous)
    if previous is not None:
        bind_terminal(generation.control_context(ctx))
    return _complete_transaction(ctx, journal, outcome="rolled_back")


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
        installed = state.read_installed(ctx)
        installed_command = state.require_command(installed) if installed is not None else None
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
    previous = _prior_selection(journal)
    if previous is None or not generation.path(ctx, previous.active).is_dir():
        raise errors.RecoveryStateError("payload recovery prior selected generation is unavailable")
    previous_ctx = generation.context(ctx, previous.active)
    identity_before = identity.committed_payload(Path(previous_ctx.executable))
    if identity_before is None or not _runtime_matches_projection(runtime, identity_before):
        raise errors.RecoveryStateError(
            "payload recovery runtime does not match the prior selected generation"
        )
    _restore_prior_projection(ctx, journal, previous)
    bind_terminal(generation.control_context(ctx))
    return _complete_transaction(ctx, journal, outcome="rolled_back")


def _reuses_retained_generation(journal: Mapping[str, object]) -> bool:
    """Return whether a reverse transition targets the selected predecessor itself."""
    return journal.get("transaction_id") == journal.get("previous_predecessor")


class PayloadTransaction:
    """Single-use owner of payload, receipt, state, journal, and rollback."""

    __slots__ = (
        "_blobs",
        "_candidate_ctx",
        "_ctx",
        "_fresh",
        "_previous_generation",
        "_previous_predecessor",
        "_previous_selection",
        "_receipt",
        "_receipt_sha256",
        "_retained_identity",
        "_reuse_generation",
        "_state",
        "_transaction_id",
        "_version",
    )
    _TOKEN = object()
    _blobs: tuple[artifact.ArtifactFile, ...]
    _ctx: runtime_context.RuntimeContext
    _fresh: bool
    _receipt: ReadOnlyJsonObject
    _receipt_sha256: str
    _state: str
    _transaction_id: str
    _version: str

    def __init__(
        self,
        *,
        ctx: runtime_context.RuntimeContext,
        blobs: tuple[artifact.ArtifactFile, ...],
        version: str,
        receipt_sha256: str,
        receipt: ReadOnlyJsonObject,
        transaction_id: str,
        fresh: bool,
        previous_generation: str | None,
        reuse_generation: bool = False,
        retained_identity: identity.LoadedPayloadIdentity | None = None,
        _token: object | None = None,
    ) -> None:
        """Construct a transaction through the private admission token."""
        if _token is not self._TOKEN:
            raise TypeError("PayloadTransaction is sealed; use begin_transaction()")
        self._ctx = ctx
        self._blobs = blobs
        self._version = version
        self._receipt_sha256 = receipt_sha256
        self._receipt = receipt
        self._transaction_id = transaction_id
        self._fresh = fresh
        self._state = "prepared"
        self._candidate_ctx = generation.context(ctx, transaction_id)
        self._reuse_generation = reuse_generation
        self._retained_identity = retained_identity
        self._previous_generation = previous_generation
        self._previous_selection = generation.read(ctx)
        if reuse_generation and (
            self._previous_selection is None
            or self._previous_selection.predecessor != transaction_id
        ):
            raise errors.InstallError("retained rollback selection changed before transition")
        self._previous_predecessor = (
            self._previous_selection.predecessor if self._previous_selection is not None else None
        )

    @property
    def release(self) -> str:
        """Return the candidate strict release version."""
        return self._version

    @property
    def receipt_sha256(self) -> str:
        """Return the candidate canonical receipt digest."""
        return self._receipt_sha256

    @property
    def expected(self) -> dict[str, object]:
        """Return source-side successor identity for lifecycle proof."""
        if self._retained_identity is not None:
            return {
                "transaction_id": self._transaction_id,
                **self._retained_identity.handoff(),
            }
        manifest = payload_candidate.manifest_for(self._version, self._blobs, self._receipt_sha256)
        return {
            "transaction_id": self._transaction_id,
            "release": self._version,
            "manifest_sha256": hashlib.sha256(projection.manifest_bytes(manifest)).hexdigest(),
            "serving_payload_sha256": manifest["serving_payload_sha256"],
            "release_receipt_sha256": self._receipt_sha256,
        }

    @property
    def context(self) -> runtime_context.RuntimeContext:
        """Return the immutable candidate runtime context."""
        return self._candidate_ctx

    def commit_projection(self) -> None:
        """Materialize one immutable candidate without changing the active generation."""
        if self._state != "prepared":
            raise errors.InstallError("payload transaction is not prepared")
        self._owned_journal()
        rollback = state.transaction_root(self._ctx) / "rollback"
        rollback.mkdir(mode=0o700)
        mutated = False
        try:
            if self._reuse_generation:
                candidate = identity.committed_payload(Path(self._candidate_ctx.executable))
                if candidate != self._retained_identity:
                    raise errors.InstallError(
                        "retained rollback predecessor changed before materialization"
                    )
            control_ctx = generation.control_context(self._ctx)
            command_snapshot = command.snapshot(
                Path(self._ctx.command), Path(control_ctx.executable)
            )
            command.write_snapshot(rollback, command_snapshot)
            if self._reuse_generation:
                payload_candidate.prewarm(self._candidate_ctx)
                self._state = "materialized"
                self._write_journal()
                return
            candidate_paths = {blob.path for blob in self._blobs}
            payload_candidate.reject_unowned_collisions(
                self._candidate_ctx,
                frozenset(),
                candidate_paths,
            )
            mutated = True
            payload_candidate.write_projection(
                self._candidate_ctx,
                self._blobs,
                self._version,
                self._receipt,
                self._receipt_sha256,
            )
            runtime_spec.write(self._candidate_ctx)
            ok, detail = projection.verify_payload_manifest(self._candidate_ctx)
            if not ok:
                raise errors.InstallError(f"committed payload integrity check failed: {detail}")
            payload_candidate.prewarm(self._candidate_ctx)
            self._state = "materialized"
            self._write_journal()
        except BaseException:
            if not mutated:
                _complete_transaction(self._ctx, self._owned_journal(), outcome="closed")
                self._state = "rolled_back"
            else:
                try:
                    self.rollback()
                except errors.InstallError as rollback_exc:
                    raise errors.InstallError(
                        "payload commit failed and rollback failed"
                    ) from rollback_exc
            raise

    def activate(self) -> None:
        """Atomically select and project the already materialized candidate."""
        if self._state != "materialized":
            raise errors.InstallError("payload transaction is not materialized")
        self._owned_journal()
        rollback = state.transaction_root(self._ctx) / "rollback"
        command_snapshot = command.read_snapshot(rollback)
        predecessor = (
            self._previous_selection.active if self._previous_selection is not None else None
        )
        generation.select(
            self._ctx,
            active=self._transaction_id,
            predecessor=predecessor,
        )
        control_ctx = generation.control_context(self._ctx)
        command.project(
            Path(self._ctx.command),
            Path(control_ctx.executable),
            previous=command_snapshot,
        )
        self._state = "activated"
        state.write_journal(
            self._ctx,
            transaction_id=self._transaction_id,
            version=self._version,
            receipt_sha256=self._receipt_sha256,
            state=self._state,
            fresh=self._fresh,
            previous_generation=self._previous_generation,
            previous_predecessor=self._previous_predecessor,
        )

    def finalize(self, runtime: Mapping[str, object] | None = None) -> None:
        """Record installation success only after the caller proves SERVING."""
        if self._state != "activated":
            raise errors.InstallError("payload transaction is not activated")
        journal = self._owned_journal()
        try:
            _finalize_installation(
                self._ctx,
                journal=journal,
                runtime=runtime or {},
                bind_terminal=None,
            )
        except BaseException:
            self._state = "recovery_required"
            raise
        self._state = "finalized"

    def rollback(self) -> None:
        """Restore the exact prior payload, receipt, state, or their absence."""
        if self._state == "rolled_back":
            return
        if self._state not in {"prepared", "materialized", "activated"}:
            raise errors.InstallError(
                f"payload transaction in {self._state} state cannot be rolled back"
            )
        journal = self._owned_journal(for_rollback=True)
        if journal["state"] in {"closed", "rolled_back"}:
            _finish_cleanup(self._ctx, journal)
            self._state = "rolled_back"
            return
        if (state.transaction_root(self._ctx) / "rollback").exists():
            _restore_prior_projection(self._ctx, journal, self._previous_selection)
        _complete_transaction(self._ctx, journal, outcome="rolled_back")
        self._state = "rolled_back"

    def rollback_if_prepared(self) -> bool:
        """Close the transaction only before any projection mutation begins."""
        if self._state != "prepared":
            return False
        self.rollback()
        return True

    def preserve_for_recovery(self, reason: str) -> None:
        """Keep committed bytes and rollback while marking an unknown outcome."""
        if self._state not in {"materialized", "activated"}:
            raise errors.InstallError("only a materialized transaction can require recovery")
        self._owned_journal()
        phase = self._state
        self._state = "recovery_required"
        state.write_journal(
            self._ctx,
            transaction_id=self._transaction_id,
            version=self._version,
            receipt_sha256=self._receipt_sha256,
            state=self._state,
            fresh=self._fresh,
            previous_generation=self._previous_generation,
            previous_predecessor=(self._previous_predecessor),
            phase=phase,
            reason=reason,
        )

    def _owned_journal(self, *, for_rollback: bool = False) -> Mapping[str, object]:
        journal = state.read_journal(self._ctx)
        if journal["transaction_id"] != self._transaction_id:
            raise errors.InstallError("payload transaction no longer owns the current journal")
        if journal["state"] in state.TERMINAL_STATES and not (
            for_rollback and journal["state"] in {"closed", "rolled_back"}
        ):
            raise errors.InstallError("payload transaction is terminal; complete recovery")
        return journal

    def _write_journal(self) -> None:
        state.write_journal(
            self._ctx,
            transaction_id=self._transaction_id,
            version=self._version,
            receipt_sha256=self._receipt_sha256,
            state=self._state,
            fresh=self._fresh,
            previous_generation=self._previous_generation,
            previous_predecessor=self._previous_predecessor,
        )


def begin_transaction(
    ctx: runtime_context.RuntimeContext,
    candidate: artifact.VerifiedArtifact,
) -> PayloadTransaction:
    """Claim one admitted release and create its private transaction journal."""
    return _begin_transaction(ctx, candidate)


def begin_rollback_transaction(
    ctx: runtime_context.RuntimeContext,
    retained: payload_rollback.RetainedRollback,
) -> PayloadTransaction:
    """Begin one reverse transition from a verified retained predecessor."""
    previous = state.read_installed(ctx)
    if previous is None or state.require_version(previous) != retained.successor.release:
        raise errors.InstallError("retained rollback successor changed before transition")
    selection = generation.read(ctx)
    if selection is None or selection.predecessor is None:
        raise errors.InstallError("retained rollback selection changed before transition")
    predecessor_ctx = generation.context(ctx, selection.predecessor)
    if Path(predecessor_ctx.payload_dir) != retained.root:
        raise errors.InstallError("retained rollback predecessor changed before transition")
    _claim_transaction_root(ctx)
    state.write_journal(
        ctx,
        transaction_id=selection.predecessor,
        version=retained.predecessor.release,
        receipt_sha256=retained.predecessor.release_receipt_sha256,
        state="prepared",
        fresh=False,
        previous_generation=selection.active,
        previous_predecessor=selection.predecessor,
    )
    return PayloadTransaction(
        ctx=ctx,
        blobs=(),
        version=retained.predecessor.release,
        receipt_sha256=retained.predecessor.release_receipt_sha256,
        receipt={},
        transaction_id=selection.predecessor,
        fresh=False,
        previous_generation=selection.active,
        reuse_generation=True,
        retained_identity=retained.predecessor,
        _token=PayloadTransaction._TOKEN,
    )


def _begin_transaction(
    ctx: runtime_context.RuntimeContext,
    candidate: artifact.VerifiedArtifact,
) -> PayloadTransaction:
    """Claim one admitted payload for a forward transition."""
    try:
        blobs, version, receipt_sha256, receipt, _sidecar = artifact.claim(candidate)
    except artifact.ArtifactError as exc:
        raise errors.InstallError(str(exc)) from exc
    payload_candidate.validate(blobs, version, receipt_sha256, receipt)
    previous = state.read_installed(ctx)
    current_selection = generation.read(ctx)
    if previous is not None:
        if current_selection is None:
            raise errors.InstallError(
                "upgrade requires a selected payload generation; "
                "uninstall the existing payload explicitly before reinstalling"
            )
        control_identity = identity.committed_payload(
            Path(generation.control_context(ctx).executable)
        )
        if control_identity is None:
            raise errors.InstallError("installed control-plane identity is invalid")
        comparison = state.compare_versions(version, control_identity.release)
        if comparison < 0:
            raise errors.InstallError("released payload downgrade is refused")
        if comparison == 0:
            raise errors.InstallError("released payload replay is refused")
    install_root = Path(ctx.install_dir)
    fresh = previous is None and _empty_or_absent_control_root(install_root)
    if previous is None and current_selection is None and not fresh:
        raise errors.InstallError(
            "installed payload root contains unverified content; remove it explicitly before "
            "installing"
        )
    previous_generation = current_selection.active if current_selection is not None else None
    _claim_transaction_root(ctx)
    transaction_id = uuid.uuid4().hex
    state.write_journal(
        ctx,
        transaction_id=transaction_id,
        version=version,
        receipt_sha256=receipt_sha256,
        state="prepared",
        fresh=fresh,
        previous_generation=previous_generation,
        previous_predecessor=(
            current_selection.predecessor if current_selection is not None else None
        ),
    )
    return PayloadTransaction(
        ctx=ctx,
        blobs=blobs,
        version=version,
        receipt_sha256=receipt_sha256,
        receipt=receipt,
        transaction_id=transaction_id,
        fresh=fresh,
        previous_generation=previous_generation,
        _token=PayloadTransaction._TOKEN,
    )


def _empty_or_absent_control_root(root: Path) -> bool:
    """Return whether no installed or operator-owned state occupies the control root."""
    if not root.exists() and not root.is_symlink():
        return True
    if root.is_symlink() or not root.is_dir():
        return False
    try:
        return next(root.iterdir(), None) is None
    except OSError as exc:
        raise errors.InstallError("installed payload root is unreadable") from exc


def _claim_transaction_root(ctx: runtime_context.RuntimeContext) -> Path:
    root = state.transaction_root(ctx)
    transaction_state = state.status(ctx)
    if transaction_state is not None:
        if transaction_state.get("state") != "invalid":
            raise errors.RecoveryRequiredError("complete payload recovery before installing")
        raise errors.RecoveryStateError(
            "payload transaction evidence is invalid; preserve it for diagnosis"
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    return root


def _complete_transaction(
    ctx: runtime_context.RuntimeContext,
    journal: Mapping[str, object],
    *,
    outcome: Literal["closed", "rolled_back", "finalized"],
) -> dict[str, object]:
    """Persist terminal effects before deleting any recovery input."""
    terminal = {key: value for key, value in journal.items() if key not in {"phase", "reason"}}
    terminal["state"] = outcome
    owned_files.write_bytes(state.journal_path(ctx), digest.canonical_json(terminal), mode=0o600)
    return _finish_cleanup(ctx, terminal)


def purge(ctx: runtime_context.RuntimeContext) -> dict[str, object]:
    """Record exact disposal authority after supervision and processes have exited."""
    if state.status(ctx) is not None:
        raise errors.RecoveryRequiredError("complete payload recovery before uninstalling")
    root = Path(ctx.install_dir)
    files: JsonObject = {}
    for selected in generation.owned_contexts(ctx) or (ctx,):
        payload_root = Path(selected.payload_dir)
        for relative, expected in projection.owned_payload_files(selected).items():
            files[(payload_root / relative).relative_to(root).as_posix()] = expected
    installed = state.read_installed(ctx)
    if installed is None:
        manifest = owned_files.read_json_object(
            projection.payload_manifest_path(ctx), "installed payload manifest"
        )
        version = manifest["release"]
        receipt_sha256 = manifest["release_receipt_sha256"]
    else:
        version = state.require_version(installed)
        receipt_sha256 = installed["receipt_sha256"]
    for target in (generation.selector_path(ctx), state.installed_path(ctx)):
        if target.exists() or target.is_symlink():
            relative = target.relative_to(root).as_posix()
            files[relative] = digest.sha256_file(
                owned_files.regular_file(root, relative, "payload removal")
            )
    journal: JsonObject = {
        "schema_version": state.TRANSACTION_JOURNAL_SCHEMA,
        "transaction_id": uuid.uuid4().hex,
        "version": version,
        "receipt_sha256": receipt_sha256,
        "fresh": False,
        "state": "purged",
        "files": files,
    }
    state.removal_files(journal)
    owned_files.write_bytes(state.journal_path(ctx), digest.canonical_json(journal), mode=0o600)
    return _finish_cleanup(ctx, journal)


def _finish_cleanup(
    ctx: runtime_context.RuntimeContext, journal: Mapping[str, object]
) -> dict[str, object]:
    """Resume only disposal; terminal projections and supervision remain unchanged."""
    match journal["state"]:
        case "purged":
            files = state.removal_files(journal)
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
            generation.prune(ctx, selection)
        case "rolled_back":
            if not _reuses_retained_generation(journal):
                generation.remove(ctx, str(journal["transaction_id"]))
            install = Path(ctx.install_dir)
            if journal["fresh"] is True and install.is_dir() and not any(install.iterdir()):
                install.rmdir()
        case "closed":
            pass
        case _:
            raise errors.RecoveryStateError("payload transaction is not terminal")
    _remove_transaction_root(ctx)
    try:
        state.journal_path(ctx).unlink()
    except OSError as exc:
        raise errors.InstallError("payload transaction cleanup failed") from exc
    return {
        "transaction_id": journal["transaction_id"],
        "version": journal["version"],
        "state": journal["state"],
    }


def _remove_transaction_root(ctx: runtime_context.RuntimeContext) -> None:
    root = state.transaction_root(ctx)
    if not root.exists():
        return
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise errors.InstallError("payload transaction cleanup failed") from exc
    if root.exists():
        raise errors.InstallError("payload transaction cleanup did not remove the transaction")
