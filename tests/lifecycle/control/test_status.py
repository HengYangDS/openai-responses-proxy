"""Installed lifecycle status contracts."""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.cli import application
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import roles as serving_roles
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import healthy_status_dependencies
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import running_supervisor


def test_status_distinguishes_invalid_evidence_from_runtime_degradation(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    Path(ctx.install_dir).mkdir(parents=True)
    mocker.patch.object(control, "adapter").return_value.status.return_value = "absent"
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "absent", "kind": None},
    )
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(False, "installed payload manifest is unavailable"),
    )

    degraded = control.status(ctx)

    assert degraded["state"] == "degraded"
    assert degraded["detail"] == "installed payload manifest is unavailable"

    transaction_root = Path(records.transaction_root(ctx))
    transaction_root.mkdir()

    invalid = control.status(ctx)

    assert invalid["state"] == "invalid"
    assert invalid["detail"] == "payload transaction journal is missing"


def test_status_reports_an_invalid_installed_state_without_losing_read_only_evidence(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_root = Path(ctx.install_dir)
    install_root.mkdir(parents=True)
    Path(records.installed_path(ctx)).write_text("not-json", encoding="utf-8")
    mocker.patch.object(control, "adapter").return_value.status.return_value = "absent"
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "absent", "kind": None},
    )
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(False, "installed payload manifest is unavailable"),
    )

    result = control.status(ctx)

    assert result["state"] == "invalid"
    assert result["release"] is None
    assert result["detail"] == "installed release state is unavailable or invalid"


def test_status_distinguishes_absence_from_command_and_transaction_degradation(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)
    service = mocker.patch.object(control, "adapter").return_value
    service.status.return_value = "absent"
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(control, "read_runtime", return_value=None)
    command_status = mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "absent", "kind": None},
    )
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(False, "installed payload manifest is unavailable"),
    )

    absent = control.status(ctx)
    assert absent["state"] == "not_installed"
    assert absent["detail"] == "not installed"

    mocker.stopall()
    installed_ctx = install_context(tmp_path / "installed")
    install_payload(installed_ctx, "1.2.2", mocker=mocker)
    healthy_status_dependencies(installed_ctx, mocker=mocker)
    mocker.patch.object(
        control.projection,
        "verify_payload_manifest",
        return_value=(True, "ok"),
    )
    command_status = mocker.patch.object(
        control.command,
        "status",
        return_value={
            "path": installed_ctx.command,
            "state": "foreign",
            "kind": "file",
        },
    )
    with subtests.test(state="command ownership unavailable"):
        degraded = control.status(installed_ctx)
        assert degraded["state"] == "degraded"
        assert degraded["detail"] == "native command ownership is unavailable"

    command_status.return_value = {
        "path": installed_ctx.command,
        "state": "owned",
        "kind": "symlink",
    }
    mocker.patch.object(records, "status", return_value={"state": "future"})
    with subtests.test(state="unrecognized transaction"):
        degraded = control.status(installed_ctx)
        assert degraded["state"] == "degraded"
        assert degraded["detail"] == "installation is degraded"


def test_status_classifies_a_verified_installation_whose_supervisor_is_stopped(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    control_executable = generation.control_context(ctx).executable
    native = mocker.patch.object(control, "adapter").return_value
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    port_listeners = mocker.patch.object(control.process, "listener_pids")
    mocker.patch.object(control, "read_runtime", return_value=None)
    mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "owned", "kind": "symlink"},
    )

    def observe(
        *,
        service: str = "installed",
        configured: object = control_executable,
        listeners: tuple[int, ...] = (),
        port: int = ctx.port,
    ) -> dict[str, object]:
        native.status.return_value = service
        native.configured_executable.side_effect = (
            configured if isinstance(configured, Exception) else None
        )
        native.configured_executable.return_value = configured
        port_listeners.return_value = list(listeners)
        return control.status(replace(ctx, port=port))

    stopped = observe()

    assert stopped["state"] == "stopped"
    assert stopped["detail"] == "native supervisor is stopped"
    assert stopped["service"] == "installed"
    assert stopped["runtime"] is None
    assert stopped["listener_pids"] == []
    native.configured_executable.assert_called_with(generation.control_context(ctx))
    foreign = str(tmp_path / "foreign" / "openai-responses-proxy")
    unproved = errors.InstallError("carrier is unproved")
    bound = control_executable
    installed = "native service is installed"
    unavailable = "listener runtime identity is unavailable"
    for case, service, configured, listeners, port, detail in (
        ("foreign target", "installed", foreign, (), ctx.port, installed),
        ("missing target", "installed", None, (), ctx.port, installed),
        ("unproved target", "installed", unproved, (), ctx.port, installed),
        ("occupied port", "installed", bound, (4242,), ctx.port, installed),
        ("other port", "installed", bound, (), ctx.port + 1, installed),
        ("absent service", "absent", bound, (), ctx.port, "native service is absent"),
        ("unknown service", "unknown", bound, (), ctx.port, "native service is unknown"),
        ("running watchdog", "running", bound, (), ctx.port, unavailable),
    ):
        with subtests.test(case=case):
            degraded = observe(
                service=service, configured=configured, listeners=listeners, port=port
            )
            assert degraded["state"] == "degraded"
            assert degraded["detail"] == detail


def test_status_reports_retained_rollback_availability_and_corruption(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = payload_rollback.load_retained(ctx)
    healthy_status_dependencies(ctx, mocker=mocker)

    available = control.status(ctx)

    assert available["rollback"] == {
        "state": "available",
        "from_release": "1.2.3",
        "to_release": "1.2.2",
    }

    Path(retained.root, "bin", "openai-responses-proxy").write_bytes(b"invalid")

    invalid = control.status(ctx)

    assert invalid["state"] == "invalid"
    assert invalid["rollback"] == {
        "state": "invalid",
        "detail": "retained rollback predecessor generation identity is invalid",
    }


def test_status_defers_retained_authority_to_an_active_transaction(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    middle = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    middle.commit_projection()
    middle.activate()
    middle.finalize({"pid": 2})
    latest = begin_transaction(ctx, released_artifact("1.2.4"), mocker=mocker)
    latest.commit_projection()
    healthy_status_dependencies(ctx, mocker=mocker)

    result = control.status(ctx)

    assert result["state"] == "recovery_required"
    assert result["rollback"] == {
        "state": "deferred",
        "detail": "payload transaction owns rollback finalization",
    }


@pytest.mark.parametrize("outcome", ["closed", "rolled_back", "finalized"])
def test_status_routes_terminal_cleanup_to_recovery(
    tmp_path: Path, outcome: str, *, mocker
) -> None:
    """A valid disposal hold is recoverable rather than installation damage."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    pending = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    journal = records.read_journal(ctx)
    candidate_files = journal["files"]
    journal["state"] = outcome
    if outcome == "closed":
        journal.pop("files")
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))
    healthy_status_dependencies(ctx, mocker=mocker)

    result = control.status(ctx)

    assert result["state"] == "recovery_required"
    assert result["detail"] == "payload cleanup is required"
    assert records.read_journal(ctx)["state"] == outcome
    journal["state"] = "prepared"
    journal["files"] = candidate_files
    records.journal_path(ctx).write_bytes(payload_digest.canonical_json(journal))
    pending.rollback()


def test_control_status_includes_secret_free_runtime_when_listener_is_available(*, mocker):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = install_context(root)
        transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
        transaction.commit_projection()
        transaction.activate()
        transaction.finalize({"pid": 1})
        committed = identity.committed_payload(Path(generation.selected_context(ctx).executable))
        assert committed is not None
        runtime = {
            "pid": 1,
            **committed.handoff(),
            "payload_manifest_sha256": committed.manifest_sha256,
            "uptime_seconds": 12,
            "active_responses": 0,
            "counters": {},
            "upstream_classifications": {},
            "last_failure": None,
            "handoff_protocol_version": 2,
            "handoff_state": "idle",
            "handoff_transaction_id": None,
            "accepting": True,
            "draining": False,
        }
        mocker.patch.object(control, "read_runtime", return_value=runtime)
        mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[1])
        evidence = control.status(ctx)
        assert evidence["runtime"] == runtime
        assert "authorization" not in json.dumps(evidence).lower()


def test_status_uses_verified_runtime_identity_when_tcp_owner_projection_lags(
    tmp_path: Path, *, mocker
) -> None:
    """A loopback runtime proof is authoritative after portable handoff."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 1})
    active = generation.selected_context(ctx)
    committed = identity.committed_payload(Path(active.executable))
    assert committed is not None
    runtime = {
        "pid": 76541,
        **committed.handoff(),
        "payload_manifest_sha256": committed.manifest_sha256,
        "handoff_protocol_version": 2,
        "handoff_state": "finalized",
        "handoff_transaction_id": None,
        "accepting": True,
        "draining": False,
    }
    owned = process.OwnedProcess(76541, active.executable, 1.0)
    mocker.patch.object(control, "read_runtime", return_value=runtime)
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])
    capture = mocker.patch.object(control.process, "capture_executable", return_value=owned)
    running_supervisor(ctx, mocker=mocker)

    evidence = control.status(ctx)

    assert evidence["state"] == "running"
    assert evidence["listener_pids"] == []
    assert evidence["runtime"] == runtime
    capture.assert_called_once_with(
        76541,
        active.executable,
        roles={
            serving_roles.HANDOFF_CHILD_MODE,
            serving_roles.LISTENER_MODE,
        },
    )


def test_status_rejects_a_listener_serving_a_different_payload(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 1})
    foreign = {
        "pid": 1,
        "release": "9.9.9",
        "serving_payload_sha256": "1" * 64,
        "release_receipt_sha256": "2" * 64,
        "payload_manifest_sha256": "3" * 64,
        "handoff_protocol_version": 2,
        "handoff_state": "idle",
        "handoff_transaction_id": None,
        "accepting": True,
        "draining": False,
    }
    mocker.patch.object(control, "read_runtime", return_value=foreign)
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[1])
    running_supervisor(ctx, mocker=mocker)

    evidence = control.status(ctx)

    assert evidence["state"] == "degraded"
    assert evidence["detail"] == "listener runtime identity is unavailable"
    assert evidence["runtime"] is None


def test_status_rejects_a_non_accepting_or_draining_runtime(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 1})
    committed = identity.committed_payload(Path(generation.selected_context(ctx).executable))
    assert committed is not None
    healthy = {
        "pid": 1,
        **committed.handoff(),
        "payload_manifest_sha256": committed.manifest_sha256,
        "handoff_protocol_version": 2,
        "handoff_state": "idle",
        "handoff_transaction_id": None,
        "accepting": True,
        "draining": False,
    }
    runtime = mocker.patch.object(control, "read_runtime")
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[1])
    running_supervisor(ctx, mocker=mocker)

    for overrides in ({"accepting": False}, {"draining": True}):
        with subtests.test(overrides=overrides):
            runtime.return_value = {**healthy, **overrides}
            evidence = control.status(ctx)
            assert evidence["state"] == "degraded"
            assert evidence["detail"] == "listener runtime identity is unavailable"
            assert evidence["runtime"] is None


def test_status_rejects_runtime_from_an_unowned_listener(tmp_path: Path, *, mocker):
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 1})
    foreign = {
        "pid": 76541,
        "release": "foreign",
        "serving_payload_sha256": "1" * 64,
        "release_receipt_sha256": "2" * 64,
        "payload_manifest_sha256": "3" * 64,
        "accepting": True,
    }
    mocker.patch.object(control, "read_runtime", return_value=foreign)
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[])

    evidence = control.status(ctx)

    assert evidence["listener_pids"] == []
    assert evidence["runtime"] is None


def test_control_status_json_reports_recovery_without_private_transaction_data(*, mocker):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = install_context(root)
        initial = begin_transaction(ctx, released_artifact("1.2.2"), mocker=mocker)
        initial.commit_projection()
        initial.activate()
        initial.finalize({"pid": 1})
        transaction = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
        transaction.commit_projection()
        transaction.preserve_for_recovery("handoff outcome unknown")
        journal_path = Path(records.journal_path(ctx))
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal.update(
            {
                "authorization": "Bearer secret-token",
                "request_body": {"input": "private request"},
                "stage_path": "/private/release-stage",
                "reason": (
                    "handoff unknown; Authorization=Bearer secret-token; "
                    "body=private request; stage=/private/release-stage"
                ),
            }
        )
        journal_path.write_bytes(payload_digest.canonical_json(journal))
        before = journal_path.read_bytes()
        mocker.patch.object(application.runtime_context, "create", return_value=ctx)
        mocker.patch.object(application.control, "read_runtime", return_value=None)
        adapter = mocker.patch.object(application.control, "adapter")
        adapter.return_value.status.return_value = "running"
        evidence = application.dispatch("status", port=ctx.port)
        transaction_evidence = evidence["payload_transaction"]
        assert isinstance(transaction_evidence, dict)
        assert transaction_evidence == {
            "state": "invalid",
            "detail": "payload transaction journal fields are invalid",
        }
        assert "reason" not in transaction_evidence
        rendered = json.dumps(evidence)
        for forbidden in (
            "secret-token",
            "private request",
            "/private/release-stage",
        ):
            assert forbidden not in rendered
        assert journal_path.read_bytes() == before


def test_status_requires_a_current_supervisor_projection_bound_to_control(
    tmp_path: Path, *, mocker, subtests
) -> None:
    ctx = install_context(tmp_path)
    install_payload(ctx, mocker=mocker)
    healthy_status_dependencies(ctx, mocker=mocker)
    native = running_supervisor(ctx, mocker=mocker)
    mocker.patch.object(
        control.command,
        "status",
        return_value={"path": ctx.command, "state": "owned", "kind": "symlink"},
    )
    control_ctx = generation.control_context(ctx)
    foreign = str(tmp_path / "foreign" / "openai-responses-proxy")
    unbound = "native supervisor is not bound to the lifecycle control executable"
    for case, configured, current, expected in (
        ("current", control_ctx.executable, True, ("running", "running", "healthy")),
        (
            "outdated",
            control_ctx.executable,
            False,
            ("degraded", "outdated", "native service is outdated"),
        ),
        ("missing", None, True, ("degraded", "running", unbound)),
        ("foreign", foreign, True, ("degraded", "running", unbound)),
    ):
        with subtests.test(case=case):
            native.configured_executable.return_value = configured
            native.projection_is_current.return_value = current
            observed = control.status(ctx)
            assert (observed["state"], observed["service"], observed["detail"]) == expected
    native.projection_is_current.assert_called_with(control_ctx)
