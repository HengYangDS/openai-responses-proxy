"""Admit and execute one forward or reverse payload transition.

This state machine owns the live controller capability. Durable terminal
effects and restart recovery have separate transaction owners.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import candidate as payload_candidate
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.transaction import completion
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory


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
        rollback = records.transaction_root(self._ctx) / "rollback"
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
                completion.complete_transaction(self._ctx, self._owned_journal(), outcome="closed")
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
        journal = self._owned_journal()
        rollback = records.transaction_root(self._ctx) / "rollback"
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
        records.write_journal(
            self._ctx,
            transaction_id=self._transaction_id,
            version=self._version,
            receipt_sha256=self._receipt_sha256,
            state=self._state,
            fresh=self._fresh,
            previous_generation=self._previous_generation,
            previous_predecessor=self._previous_predecessor,
            files=records.removal_files(journal),
        )

    def finalize(self, runtime: Mapping[str, object] | None = None) -> None:
        """Record installation success only after the caller proves SERVING."""
        if self._state != "activated":
            raise errors.InstallError("payload transaction is not activated")
        journal = self._owned_journal()
        try:
            completion.finalize_installation(
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
            completion.finish_cleanup(self._ctx, journal)
            self._state = "rolled_back"
            return
        if (records.transaction_root(self._ctx) / "rollback").exists():
            completion.restore_prior_projection(self._ctx, journal, self._previous_selection)
        completion.complete_transaction(self._ctx, journal, outcome="rolled_back")
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
        journal = self._owned_journal()
        phase = self._state
        self._state = "recovery_required"
        records.write_journal(
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
            files=records.removal_files(journal),
        )

    def _owned_journal(self, *, for_rollback: bool = False) -> Mapping[str, object]:
        journal = records.read_journal(self._ctx)
        if journal["transaction_id"] != self._transaction_id:
            raise errors.InstallError("payload transaction no longer owns the current journal")
        if journal["state"] in records.TERMINAL_STATES and not (
            for_rollback and journal["state"] in {"closed", "rolled_back"}
        ):
            raise errors.InstallError("payload transaction is terminal; complete recovery")
        return journal

    def _write_journal(self) -> None:
        records.write_journal(
            self._ctx,
            transaction_id=self._transaction_id,
            version=self._version,
            receipt_sha256=self._receipt_sha256,
            state=self._state,
            fresh=self._fresh,
            previous_generation=self._previous_generation,
            previous_predecessor=self._previous_predecessor,
            files=records.removal_files(self._owned_journal()),
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
    previous = records.read_installed(ctx)
    if previous is None or records.require_version(previous) != retained.successor.release:
        raise errors.InstallError("retained rollback successor changed before transition")
    selection = generation.read(ctx)
    if selection is None or selection.predecessor is None:
        raise errors.InstallError("retained rollback selection changed before transition")
    predecessor_ctx = generation.context(ctx, selection.predecessor)
    if Path(predecessor_ctx.payload_dir) != retained.root:
        raise errors.InstallError("retained rollback predecessor changed before transition")
    _claim_transaction_root(ctx)
    records.write_journal(
        ctx,
        transaction_id=selection.predecessor,
        version=retained.predecessor.release,
        receipt_sha256=retained.predecessor.release_receipt_sha256,
        state="prepared",
        fresh=False,
        previous_generation=selection.active,
        previous_predecessor=selection.predecessor,
        files={},
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
    previous = records.read_installed(ctx)
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
        comparison = records.compare_versions(version, control_identity.release)
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
    transaction_id = uuid.uuid4().hex
    candidate_ctx = generation.context(ctx, transaction_id)
    candidate_files = {blob.path: blob.sha256 for blob in blobs}
    candidate_files.update(
        {
            inventory.MANIFEST_FILENAME: hashlib.sha256(
                projection.manifest_bytes(
                    payload_candidate.manifest_for(version, blobs, receipt_sha256)
                )
            ).hexdigest(),
            inventory.RELEASE_RECEIPT_FILENAME: receipt_sha256,
            runtime_spec.FILENAME: hashlib.sha256(runtime_spec.content(candidate_ctx)).hexdigest(),
        }
    )
    _claim_transaction_root(ctx)
    records.write_journal(
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
        files={
            f"{generation.GENERATIONS_DIRNAME}/{transaction_id}/{relative}": expected
            for relative, expected in candidate_files.items()
        },
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
    root = records.transaction_root(ctx)
    transaction_state = records.status(ctx)
    if transaction_state is not None:
        if transaction_state.get("state") != "invalid":
            raise errors.RecoveryRequiredError("complete payload recovery before installing")
        raise errors.RecoveryStateError(
            "payload transaction evidence is invalid; preserve it for diagnosis"
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    return root
