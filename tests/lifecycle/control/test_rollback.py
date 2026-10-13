"""Installed lifecycle rollback contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.serving import digest as payload_digest
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import healthy_status_dependencies
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact


def test_rollback_requires_idle_and_verified_retained_authority(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)
    transaction_status = mocker.patch.object(
        payload_state, "status", return_value={"state": "prepared"}
    )

    with (
        subtests.test(state="active transaction"),
        pytest.raises(errors.RecoveryRequiredError),
    ):
        control.rollback(ctx, to_release="1.2.2")

    transaction_status.return_value = None
    retained = mocker.patch.object(payload_rollback, "load_retained_or_none")
    retained.return_value = None
    assert control.rollback(ctx, to_release="1.2.2") == {
        "state": "unavailable",
        "detail": "no verified predecessor is retained",
    }

    retained.side_effect = errors.InstallError("retained authority is corrupt")
    with (
        subtests.test(state="invalid retained authority"),
        pytest.raises(errors.RollbackStateError, match="retained authority is corrupt"),
    ):
        control.rollback(ctx, to_release="1.2.2")


def test_rollback_converges_on_one_explicit_release(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    healthy_status_dependencies(ctx, mocker=mocker)
    retained = payload_rollback.load_retained(ctx)
    load_retained = mocker.patch.object(
        payload_rollback,
        "load_retained_or_none",
        return_value=retained,
    )
    apply = mocker.patch.object(control.apply, "rollback", return_value={"state": "rolled_back"})

    assert control.rollback(ctx, to_release="1.2.3") == {
        "state": "unchanged",
        "release": "1.2.3",
    }
    load_retained.assert_not_called()
    apply.assert_not_called()

    assert control.rollback(ctx, to_release="1.2.2") == {"state": "rolled_back"}
    apply.assert_called_once()

    with pytest.raises(errors.RollbackStateError, match="requested release"):
        control.rollback(ctx, to_release="1.2.1")
    assert apply.call_count == 1


def test_rollback_rejects_an_invalid_target_before_lifecycle_inspection(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    transaction_status = mocker.patch.object(payload_state, "status")
    installed = mocker.patch.object(payload_state, "read_installed")
    retained = mocker.patch.object(payload_rollback, "load_retained_or_none")
    apply = mocker.patch.object(control.apply, "rollback")

    with pytest.raises(errors.RollbackStateError, match="requested rollback release is invalid"):
        control.rollback(ctx, to_release="latest")

    transaction_status.assert_not_called()
    installed.assert_not_called()
    retained.assert_not_called()
    apply.assert_not_called()


def test_rollback_does_not_treat_stale_installed_state_as_the_active_target(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    healthy_status_dependencies(ctx, mocker=mocker)
    installed_path = Path(payload_state.installed_path(ctx))
    installed = json.loads(installed_path.read_text(encoding="utf-8"))
    installed["receipt_sha256"] = "0" * 64
    installed_path.write_bytes(
        payload_digest.canonical_json(installed),
    )
    retained = mocker.patch.object(payload_rollback, "load_retained_or_none")
    apply = mocker.patch.object(control.apply, "rollback")

    with pytest.raises(errors.RollbackStateError, match="not proven active"):
        control.rollback(ctx, to_release="1.2.3")

    retained.assert_not_called()
    apply.assert_not_called()
