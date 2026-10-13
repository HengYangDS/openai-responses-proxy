"""Read-only status and diagnosis at the public command boundary."""

from __future__ import annotations

import json
from pathlib import Path

from openai_responses_proxy.cli import application
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import state as payload_state
from openai_responses_proxy.lifecycle.supervision import process
from tests.cli.fixtures import invoke


def test_status_uses_the_read_only_lifecycle_owner(*, mocker) -> None:
    evidence = {
        "state": "running",
        "detail": "healthy",
        "release": "2.0.8",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321},
        "payload_transaction": None,
        "command": {
            "path": "/commands/openai-responses-proxy",
            "state": "owned",
            "kind": "symlink",
        },
    }
    context = mocker.patch.object(application.runtime_context, "create", return_value="context")
    status = mocker.patch.object(application.control, "status", return_value=evidence)
    code, stdout, stderr = invoke("status", "--json", "--port", "8801")
    assert code == 0
    assert json.loads(stdout) == evidence
    assert stderr == ""
    context.assert_called_once_with(port=8801)
    status.assert_called_once_with("context")


def test_status_returns_bounded_current_runtime_evidence(*, mocker) -> None:
    evidence = {
        "state": "running",
        "detail": "healthy",
        "release": "2.0.10",
        "payload_integrity": {
            "ok": True,
            "detail": "release 2.0.15; 2 files verified",
        },
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321, "accepting": True},
        "payload_transaction": None,
        "command": {
            "state": "owned",
            "kind": "symlink",
            "path": "/commands/openai-responses-proxy",
        },
    }
    mocker.patch.object(application.control, "status", return_value=evidence)
    code, stdout, stderr = invoke("status", "--json")
    assert code == 0
    assert json.loads(stdout) == evidence
    assert stderr == ""


def test_status_binds_listener_identity_to_the_installed_executable(*, mocker) -> None:
    fixture_root = Path.cwd().anchor or "/"
    context = mocker.Mock(
        port=8792,
        install_dir=str(Path(fixture_root, "product")),
        executable=str(Path(fixture_root, "product", "openai-responses-proxy")),
        command=str(Path(fixture_root, "commands", "openai-responses-proxy")),
    )
    mocker.patch.object(
        projection,
        "verify_payload_manifest",
        return_value=(True, "release 2.0.15; 2 files verified"),
    )
    mocker.patch.object(
        application.control.payload_state,
        "read_installed",
        return_value={
            "schema_version": 1,
            "version": "2.0.15",
            "command": context.command,
        },
    )
    mocker.patch.object(
        application.control.command,
        "status",
        return_value={"path": context.command, "state": "owned", "kind": "symlink"},
    )
    adapter = mocker.patch.object(application.control, "adapter")
    pids = mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[321])
    mocker.patch.object(
        application.control,
        "read_runtime",
        return_value={"pid": 321, "accepting": True},
    )
    mocker.patch.object(payload_state, "status", return_value=None)
    adapter.return_value.status.return_value = "running"
    evidence = application.control.status(context)
    payload_integrity = evidence["payload_integrity"]
    assert isinstance(payload_integrity, dict)
    assert payload_integrity["ok"]
    assert evidence["listener_pids"] == [321]
    pids.assert_called_once_with(context)


def test_doctor_classifies_an_unavailable_listener_without_false_success(
    *, mocker, subtests
) -> None:
    stopped = {
        "state": "stopped",
        "release": "2.0.8",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "installed",
        "listener_pids": [],
        "runtime": None,
        "payload_transaction": None,
        "command": {"state": "owned", "kind": "symlink"},
    }
    status = mocker.patch.object(application.control, "status")
    mocker.patch.object(application.runtime_context, "create", return_value="context")
    serving = {
        **stopped,
        "state": "degraded",
        "listener_pids": [321],
        "runtime": {"pid": 321, "accepting": True},
    }
    for evidence, state, next_command in (
        (stopped, "stopped", "openai-responses-proxy reload"),
        ({**stopped, "state": "degraded"}, "degraded", "openai-responses-proxy status --json"),
        (
            {**stopped, "state": "degraded", "service": "running"},
            "degraded",
            "openai-responses-proxy status --json",
        ),
        (
            {**stopped, "state": "degraded", "service": "absent"},
            "degraded",
            "openai-responses-proxy install --help",
        ),
        (
            {**stopped, "state": "degraded", "service": "unknown"},
            "degraded",
            "openai-responses-proxy status --json",
        ),
        (serving, "degraded", "openai-responses-proxy reload"),
        ({**serving, "service": "absent"}, "degraded", "openai-responses-proxy install --help"),
        ({**serving, "service": "unknown"}, "degraded", "openai-responses-proxy status --json"),
        ({**serving, "service": "outdated"}, "degraded", "openai-responses-proxy reload"),
    ):
        with subtests.test(state=evidence["state"], service=evidence["service"]):
            status.return_value = evidence
            code, stdout, stderr = invoke("doctor", "--json")
            report = json.loads(stdout)
            assert code == 1
            assert stderr == ""
            assert not report["ok"]
            assert report["state"] == state
            assert report["checks"]["listener"]["status"] == (
                "passed" if evidence["runtime"] else "failed"
            )
            assert report["next"] == next_command
            assert report["checks"]["service"] == {
                "status": "passed" if evidence["service"] == "running" else "failed",
                "detail": evidence["service"],
            }
            assert "Traceback" not in stdout
            assert "Warning" not in stdout


def test_doctor_classifies_a_pristine_host_without_false_failures() -> None:
    report = application._doctor(
        {
            "state": "not_installed",
            "release": None,
            "payload_integrity": {
                "ok": False,
                "detail": "installed payload manifest is unavailable",
            },
            "service": "absent",
            "listener_pids": [],
            "runtime": None,
            "payload_transaction": None,
            "command": {"state": "absent", "kind": None, "path": "/bin/proxy"},
        }
    )

    assert report == {
        "ok": False,
        "state": "not_installed",
        "next": "openai-responses-proxy install --help",
        "checks": {
            "installation": {
                "status": "failed",
                "detail": "not installed",
            }
        },
    }


def test_doctor_uses_one_state_level_next_action() -> None:
    recovery = application._doctor(
        {
            "state": "recovery_required",
            "release": "2.0.58",
            "payload_integrity": {"ok": True, "detail": "verified"},
            "service": "running",
            "listener_pids": [321],
            "runtime": {"pid": 321, "accepting": True},
            "payload_transaction": {"state": "committed"},
            "command": {"state": "owned", "kind": "symlink"},
        }
    )
    invalid = application._doctor(
        {
            "state": "invalid",
            "detail": "payload transaction journal is missing",
            "release": "2.0.58",
            "payload_integrity": {"ok": True, "detail": "verified"},
            "service": "running",
            "listener_pids": [321],
            "runtime": {"pid": 321, "accepting": True},
            "payload_transaction": {
                "state": "invalid",
                "detail": "payload transaction journal is missing",
            },
            "command": {"state": "owned", "kind": "symlink"},
        }
    )

    assert recovery["next"] == "openai-responses-proxy recover"
    assert invalid["next"] == "openai-responses-proxy status --json"
    assert all("next" not in check for check in recovery["checks"].values())

    invalid_rollback = application._doctor(
        {
            "state": "invalid",
            "release": "2.0.58",
            "payload_integrity": {"ok": True, "detail": "verified"},
            "service": "running",
            "listener_pids": [321],
            "runtime": {"pid": 321, "accepting": True},
            "payload_transaction": None,
            "rollback": {"state": "invalid", "detail": "binding mismatch"},
            "command": {"state": "owned", "kind": "symlink"},
        }
    )

    assert invalid_rollback["checks"]["rollback"] == {
        "status": "failed",
        "detail": "invalid",
    }


def test_doctor_requires_integrity_service_and_exact_listener_identity(subtests) -> None:
    healthy = {
        "state": "running",
        "detail": "healthy",
        "release": "2.0.8",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321, "accepting": True},
        "payload_transaction": None,
        "rollback": {"state": "available"},
        "command": {
            "path": "/commands/openai-responses-proxy",
            "state": "owned",
            "kind": "symlink",
        },
    }
    cases = (
        (healthy, True, ("passed", "passed", "passed", "passed")),
        (
            {
                **healthy,
                "payload_integrity": {"ok": False, "detail": "hash mismatch"},
            },
            False,
            ("failed", "passed", "passed", "passed"),
        ),
        (
            {**healthy, "service": "unknown"},
            False,
            ("passed", "failed", "passed", "passed"),
        ),
        (
            {**healthy, "runtime": None},
            False,
            ("passed", "passed", "failed", "passed"),
        ),
        (
            {
                **healthy,
                "command": {
                    "path": "/commands/openai-responses-proxy",
                    "state": "foreign",
                    "kind": "symlink",
                },
            },
            False,
            ("passed", "passed", "passed", "failed"),
        ),
    )
    for evidence, expected_ok, statuses in cases:
        with subtests.test(evidence=evidence):
            report = application._doctor(evidence)
            assert report["ok"] is expected_ok
            assert (
                tuple(
                    report["checks"][name]["status"]
                    for name in ("payload", "service", "listener", "command")
                )
                == statuses
            )


def test_doctor_reuses_status_listener_proof_when_process_inventory_lags() -> None:
    """Doctor must not contradict the status owner's verified runtime."""
    evidence = {
        "state": "running",
        "release": "3.1.3",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "listener_pids": [],
        "runtime": {"pid": 321, "accepting": True},
        "payload_transaction": None,
        "command": {
            "path": "/commands/openai-responses-proxy",
            "state": "owned",
            "kind": "symlink",
        },
    }

    report = application._doctor(evidence)

    assert report["ok"] is True
    assert report["checks"]["listener"] == {
        "status": "passed",
        "detail": "accepting",
    }
