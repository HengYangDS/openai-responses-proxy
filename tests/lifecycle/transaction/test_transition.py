"""Payload transaction transition behavior and ownership contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import generation as payload_generation
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle import transaction as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity as listener_identity
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import runtime_files


def test_commit_prewarms_the_exact_candidate_executable(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    candidate = released_artifact()
    prewarm = mocker.patch.object(payload_transaction.payload_candidate, "prewarm")

    transaction = payload_transaction.begin_transaction(ctx, candidate)
    prewarm.assert_not_called()

    transaction.commit_projection()

    prewarm.assert_called_once_with(transaction.context)
    transaction.rollback()


def test_upgrade_rollback_removes_candidate_only_runtime_members(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    base = released_artifact("1.2.3")
    content = b"candidate-only-runtime"
    extra = artifact.ArtifactFile(
        path="bin/_internal/runtime.dat",
        mode="100644",
        blob_oid=hashlib.sha256(content).hexdigest(),
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )
    blobs = (*base.peek_blobs(), extra)
    serving = {item.path: item.sha256 for item in blobs}
    receipt = {
        "schema_version": 1,
        "version": "1.2.3",
        "serving_payload_sha256": payload_projection.manifest_serving_payload_sha256(serving),
        "serving_files": [item.path for item in blobs],
        "payload": [
            {
                "path": item.path,
                "mode": item.mode,
                "blob_oid": item.blob_oid,
                "sha256": item.sha256,
            }
            for item in blobs
        ],
    }
    receipt_sha256 = hashlib.sha256(payload_digest.canonical_json(receipt)).hexdigest()
    candidate = artifact.mint(
        blobs,
        receipt,
        {
            "schema_version": 1,
            "algorithm": "sha256",
            "receipt_sha256": receipt_sha256,
            "serving_payload_sha256": receipt["serving_payload_sha256"],
        },
    )
    transaction = begin_transaction(ctx, candidate, mocker=mocker)
    transaction.commit_projection()
    introduced = Path(transaction.context.payload_dir, extra.path)
    assert introduced.read_bytes() == content
    transaction.rollback()
    assert not introduced.exists()


def test_finalize_selects_no_predecessor_for_a_fresh_install(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)

    install_payload(ctx, "1.2.2", mocker=mocker)

    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.predecessor is None
    assert tuple(payload_generation.root(ctx).iterdir()) == (
        payload_generation.path(ctx, selection.active),
    )


def test_finalize_retains_exactly_the_displaced_predecessor(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)

    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})

    retained = payload_rollback.load_retained(ctx)
    assert retained.predecessor.release == "1.2.2"
    assert retained.successor.release == "1.2.3"
    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.predecessor is not None
    assert retained.root == payload_generation.path(ctx, selection.predecessor)


def test_a_later_finalize_replaces_the_retained_predecessor(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    middle = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    middle.commit_projection()
    middle.activate()
    middle.finalize({"pid": 2})
    first = payload_rollback.load_retained(ctx)
    latest = begin_transaction(ctx, released_artifact("1.2.4"), mocker=mocker)

    latest.commit_projection()
    latest.activate()
    latest.finalize({"pid": 3})

    retained = payload_rollback.load_retained(ctx)
    assert retained.predecessor.release == "1.2.3"
    assert retained.successor.release == "1.2.4"
    assert retained.root != first.root
    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.predecessor is not None
    assert retained.root == payload_generation.path(ctx, selection.predecessor)


def test_retained_predecessor_reuses_an_exact_reverse_transaction(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    mocker.patch.object(payload_transaction.payload_candidate, "prewarm")
    first = payload_transaction.begin_transaction(ctx, released_artifact("1.2.2"))
    first.commit_projection()
    first.activate()
    first.finalize({"pid": 1})
    successor = payload_transaction.begin_transaction(ctx, released_artifact("1.2.3"))
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    control_executable = Path(ctx.command).resolve(strict=True)
    assert control_executable.samefile(successor.context.executable)

    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    reverse.commit_projection()

    restored = listener_identity.committed_payload(Path(reverse.context.executable))
    assert restored is not None
    assert restored.release == "1.2.2"
    reverse.activate()
    assert Path(ctx.command).resolve(strict=True).samefile(control_executable)
    reverse.rollback()
    selection = payload_generation.read(ctx)
    assert selection is not None
    current = listener_identity.committed_payload(
        Path(payload_generation.context(ctx, selection.active).executable)
    )
    assert current is not None
    assert current.release == "1.2.3"


def test_finalized_reverse_transition_keeps_newest_control_upgrade_floor(
    tmp_path: Path, *, mocker
) -> None:
    """Serving rollback cannot make an older release the install authority."""
    ctx = install_context(tmp_path)
    mocker.patch.object(payload_transaction.payload_candidate, "prewarm")
    first = payload_transaction.begin_transaction(ctx, released_artifact("1.2.2"))
    first.commit_projection()
    first.activate()
    first.finalize({"pid": 1})
    successor = payload_transaction.begin_transaction(ctx, released_artifact("1.2.3"))
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    control_executable = Path(ctx.command).resolve(strict=True)
    reverse = payload_transaction.begin_rollback_transaction(
        ctx, payload_rollback.load_retained(ctx)
    )
    reverse.commit_projection()
    reverse.activate()
    reverse.finalize({"pid": 3})

    assert Path(ctx.command).resolve(strict=True).samefile(control_executable)
    with pytest.raises(errors.InstallError, match="replay"):
        payload_transaction.begin_transaction(ctx, released_artifact("1.2.3"))

    forward = payload_transaction.begin_transaction(ctx, released_artifact("1.2.4"))
    forward.rollback()


def test_reverse_transition_rechecks_predecessor_before_materialization(
    tmp_path: Path, *, mocker
) -> None:
    """An admitted rollback still fails if the retained generation later changes."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    reverse = payload_transaction.begin_rollback_transaction(ctx, retained)
    Path(reverse.context.executable).write_bytes(b"corrupt")

    with pytest.raises(errors.InstallError, match="changed before materialization"):
        reverse.commit_projection()


def test_fresh_commit_writes_manifest_receipt_and_pending_journal_then_finalize_state(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)

    transaction.commit_projection()

    assert Path(transaction.context.payload_dir, "payload-manifest.json").is_file()
    assert Path(transaction.context.payload_dir, inventory.RELEASE_RECEIPT_FILENAME).is_file()
    journal = json.loads(Path(payload_state.journal_path(ctx)).read_text(encoding="utf-8"))
    assert journal["state"] == "materialized"
    assert not Path(payload_state.installed_path(ctx)).exists()

    transaction.activate()
    transaction.finalize({"pid": 123, "accepting": True})

    state = json.loads(Path(payload_state.installed_path(ctx)).read_text(encoding="utf-8"))
    assert state["version"] == "1.2.3"
    assert state["receipt_sha256"] == transaction.receipt_sha256
    assert state["command"] == ctx.command
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_upgrade_materializes_an_immutable_candidate_generation(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    predecessor_bytes = b"predecessor-native-executable"
    successor_bytes = b"successor-native-executable"
    predecessor = begin_transaction(
        ctx,
        released_artifact("1.2.2", executable_content=predecessor_bytes),
        mocker=mocker,
    )
    predecessor.commit_projection()
    predecessor.activate()
    predecessor.finalize({"pid": 1})
    predecessor_executable = Path(predecessor.context.executable)

    successor = begin_transaction(
        ctx,
        released_artifact("1.2.3", executable_content=successor_bytes),
        mocker=mocker,
    )
    successor.commit_projection()

    assert predecessor_executable.read_bytes() == predecessor_bytes
    assert Path(successor.context.executable).read_bytes() == successor_bytes
    assert Path(successor.context.executable) != predecessor_executable
    assert Path(ctx.executable) != Path(successor.context.executable)


def test_generation_upgrade_does_not_copy_a_payload_snapshot(tmp_path: Path, *, mocker) -> None:
    """Selected generations remain the recovery authority during upgrade."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)

    successor.commit_projection()

    rollback = Path(payload_state.transaction_root(ctx), "rollback")
    assert {path.name for path in rollback.iterdir()} == {command.SNAPSHOT_FILENAME}
    successor.rollback()


def test_activation_selects_candidate_and_retains_only_the_predecessor(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    predecessor = begin_transaction(
        ctx,
        released_artifact("1.2.2", executable_content=b"predecessor"),
        mocker=mocker,
    )
    predecessor.commit_projection()
    predecessor.activate()
    predecessor.finalize({"pid": 1})
    ctx.executable = predecessor.context.executable
    successor = begin_transaction(
        ctx,
        released_artifact("1.2.3", executable_content=b"successor"),
        mocker=mocker,
    )
    successor.commit_projection()

    successor.activate()

    selection = payload_generation.read(ctx)
    assert selection is not None
    assert selection.active == Path(successor.context.payload_dir).name
    assert selection.predecessor == Path(predecessor.context.payload_dir).name
    assert Path(ctx.command).samefile(successor.context.executable)


def test_fresh_transaction_projects_and_rolls_back_the_user_command(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)

    transaction.commit_projection()
    transaction.activate()

    command_path = Path(ctx.command)
    runtime_config = Path(transaction.context.payload_dir, inventory.RUNTIME_CONFIG_FILENAME)
    assert command.status(command_path, Path(transaction.context.executable))["state"] == ("owned")
    assert runtime_config.is_file()

    transaction.rollback()

    assert not command_path.exists()
    assert not runtime_config.exists()


def test_upgrade_rollback_restores_the_prior_user_command_target(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    prior_target = Path(ctx.executable)
    command_path = Path(ctx.command)
    assert os.path.samefile(command_path, prior_target)

    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.rollback()

    assert command.status(command_path, prior_target)["state"] == "owned"


@pytest.mark.parametrize("upgrading", [False, True])
@pytest.mark.parametrize("failure", ["integrity", "runtime-spec", "interrupted-prewarm"])
def test_failed_commit_rolls_back_before_propagating(
    tmp_path: Path, upgrading: bool, failure: str, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    if upgrading:
        install_payload(ctx, "1.2.2", mocker=mocker)
    before = Path(ctx.executable).read_bytes() if upgrading else None
    selection = payload_generation.read(ctx)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    failure_type: type[BaseException] = errors.InstallError
    match failure:
        case "integrity":
            mocker.patch.object(
                payload_projection,
                "verify_payload_manifest",
                return_value=(False, "tampered"),
            )
        case "runtime-spec":
            failure_type = OSError
            mocker.patch.object(
                payload_transaction.runtime_spec, "write", side_effect=OSError("disk failure")
            )
        case "interrupted-prewarm":
            failure_type = KeyboardInterrupt
            mocker.patch.object(
                payload_transaction.payload_candidate, "prewarm", side_effect=KeyboardInterrupt
            )
    with pytest.raises(failure_type):
        transaction.commit_projection()
    if before is not None:
        assert Path(ctx.executable).read_bytes() == before
    else:
        assert not Path(ctx.executable).exists()
    assert payload_generation.read(ctx) == selection
    assert not Path(transaction.context.payload_dir).exists()
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_upgrade_rollback_restores_payload_receipt_and_installed_state_exactly(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    selection = payload_generation.read(ctx)
    assert selection is not None
    active = payload_generation.context(ctx, selection.active)
    before = {
        relative: Path(active.payload_dir, relative).read_bytes()
        for relative in (
            *runtime_files(),
            inventory.MANIFEST_FILENAME,
            inventory.RELEASE_RECEIPT_FILENAME,
        )
    }
    before_state = Path(payload_state.installed_path(ctx)).read_bytes()

    second = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    second.commit_projection()
    second.rollback()

    restored = payload_generation.read(ctx)
    assert restored == selection
    for relative, content in before.items():
        assert Path(active.payload_dir, relative).read_bytes() == content
    assert Path(payload_state.installed_path(ctx)).read_bytes() == before_state
    assert not Path(payload_state.transaction_root(ctx)).exists()


def test_upgrade_rollback_preserves_unknown_content(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    unknown = Path(ctx.install_dir, "proxy", "local.py")
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_bytes(b"local content\n")

    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    assert unknown.read_bytes() == b"local content\n"
    transaction.rollback()

    assert unknown.read_bytes() == b"local content\n"


def test_preserve_for_recovery_keeps_journal_and_rollback_visible(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    transaction.commit_projection()

    transaction.preserve_for_recovery("handoff outcome unknown")

    journal = json.loads(Path(payload_state.journal_path(ctx)).read_text(encoding="utf-8"))
    assert journal["state"] == "recovery_required"
    assert journal["reason"] == "handoff outcome unknown"
    assert Path(payload_state.transaction_root(ctx), "rollback").is_dir()
