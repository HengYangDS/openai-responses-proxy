"""Human and machine projections of public command results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.cli import application
from tests.cli.fixtures import invoke
from tests.lifecycle.fixtures import install_context


@pytest.mark.parametrize(
    ("command", "result"),
    [
        ("install", {}),
        ("status", {"state": "running"}),
        ("doctor", {"ok": True}),
        ("reload", {"state": "reloaded"}),
        ("rollback", {"state": "unknown"}),
        ("recover", {"state": "unknown"}),
        ("uninstall", {"state": "unknown"}),
    ],
)
def test_malformed_success_cannot_be_presented_as_completed(
    command: str, result: dict[str, object], *, mocker, capsys
) -> None:
    mocker.patch.object(application, "dispatch", return_value=result)

    code = application._execute(command, as_json=True)
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "internal_error"


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize(
    ("old_pid", "new_pid"),
    [(0, 42), (41, 0), (-1, 42), (41, -1), (41, 41), (True, 42), (41, True)],
)
def test_reload_rejects_invalid_process_transition(
    old_pid: object, new_pid: object, as_json: bool, recovered: bool, *, mocker, capsys
) -> None:
    result = {"state": "reloaded", "old_pid": old_pid, "new_pid": new_pid}
    if recovered:
        result.update({"transaction_id": "tx", "recovered_after_controller_failure": True})
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute("reload", as_json=as_json) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err
    if as_json:
        assert json.loads(captured.err)["error"]["code"] == "internal_error"
    else:
        assert "Action required" in captured.err


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("recovered", [False, True])
def test_reload_accepts_distinct_positive_processes(
    as_json: bool, recovered: bool, *, mocker, capsys
) -> None:
    result = {"state": "reloaded", "old_pid": 41, "new_pid": 42}
    if recovered:
        result.update({"transaction_id": "tx", "recovered_after_controller_failure": True})
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute("reload", as_json=as_json) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        assert json.loads(captured.out) == {**result, "next": "openai-responses-proxy status"}
    else:
        assert "Reloaded" in captured.out
        assert "41" in captured.out
        assert "42" in captured.out


@pytest.mark.parametrize("as_json", [False, True])
def test_reload_reports_a_started_stopped_installation(as_json: bool, *, mocker, capsys) -> None:
    result = {"state": "started", "new_pid": 42}
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute("reload", as_json=as_json) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        assert json.loads(captured.out) == {**result, "next": "openai-responses-proxy status"}
    else:
        assert "OpenAI Responses Proxy  Started" in captured.out
        assert "Current PID 42" in captured.out
        assert "Previous PID" not in captured.out
        assert captured.out.endswith("Next\n  openai-responses-proxy status\n")


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    "result",
    [
        {"state": "started"},
        {"state": "started", "new_pid": 0},
        {"state": "started", "new_pid": -1},
        {"state": "started", "new_pid": True},
        {"state": "started", "new_pid": "42"},
        {"state": "started", "old_pid": 41, "new_pid": 42},
    ],
)
def test_reload_rejects_an_unproved_start(
    result: dict[str, object], as_json: bool, *, mocker, capsys
) -> None:
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute("reload", as_json=as_json) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    if as_json:
        assert json.loads(captured.err)["error"]["code"] == "internal_error"
    else:
        assert "Action required" in captured.err


def test_stopped_installation_names_reload_in_status_and_doctor() -> None:
    evidence = {
        "state": "stopped",
        "detail": "native supervisor is stopped",
        "release": "5.0.2",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "command": {"state": "owned", "kind": "symlink", "path": "/bin/proxy"},
        "service": "installed",
        "listener_pids": [],
        "runtime": None,
        "payload_transaction": None,
        "rollback": {"state": "unavailable"},
    }

    status = application.outcome.admit("status", evidence)
    report = application.outcome.admit("doctor", application._doctor(evidence))
    rendered = application.presentation.render("status", status.evidence)

    assert status.state == "stopped"
    assert status.exit_code == 0
    assert report.state == "stopped"
    assert report.exit_code == 1
    assert report.evidence["next"] == "openai-responses-proxy reload"
    assert "State       Stopped" in rendered
    assert "Listener    Unavailable" in rendered
    assert rendered.endswith("Next\n  openai-responses-proxy reload")


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("command", ["uninstall", "recover"])
@pytest.mark.parametrize("stopped", [-1, True, 0.5])
def test_cleanup_rejects_invalid_process_count(
    command: str, stopped: object, as_json: bool, *, mocker, capsys
) -> None:
    result = {"state": "purged", "stopped": stopped, "command_removed": True}
    if command == "recover":
        result.update({"transaction_id": "tx", "version": "5.0.2"})
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute(command, as_json=as_json) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err
    if as_json:
        assert json.loads(captured.err)["error"]["code"] == "internal_error"
    else:
        assert "Action required" in captured.err


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("command", ["uninstall", "recover"])
def test_cleanup_accepts_zero_processes_when_no_listener_remains(
    command: str, as_json: bool, *, mocker, capsys
) -> None:
    result = {"state": "purged", "stopped": 0, "command_removed": True}
    if command == "recover":
        result.update({"transaction_id": "tx", "version": "5.0.2"})
    mocker.patch.object(application, "dispatch", return_value=result)

    assert application._execute(command, as_json=as_json) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        named = "openai-responses-proxy status" if command == "recover" else None
        assert json.loads(captured.out) == {**result, "next": named}
    else:
        assert "Purged" in captured.out


@pytest.mark.parametrize("value", [Path("/private/operator"), float("nan")])
def test_non_json_nested_evidence_is_rejected_before_projection(value: object) -> None:
    with pytest.raises(ValueError, match="finite JSON object"):
        application.outcome.admit(
            "install", {"state": "installed", "release": "4.0.5", "extra": value}
        )


@pytest.mark.parametrize("placement", ["result", "command", "payload_integrity"])
def test_status_rejects_undeclared_public_evidence(placement: str, *, mocker, capsys) -> None:
    marker = "private-evidence-marker"
    status: dict[str, object] = {
        "state": "running",
        "detail": "healthy",
        "release": "4.0.5",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "command": {"state": "owned", "kind": "symlink", "path": "/bin/proxy"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321},
        "payload_transaction": None,
    }
    target = status if placement == "result" else status[placement]
    assert isinstance(target, dict)
    target["undeclared"] = marker
    mocker.patch.object(application, "dispatch", return_value=status)

    assert application._execute("status", as_json=True) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert marker not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "internal_error"


@pytest.mark.parametrize(
    ("command", "result"),
    [
        ("install", {"state": "installed", "release": "4.0.5"}),
        (
            "doctor",
            {
                "state": "running",
                "ok": True,
                "next": None,
                "checks": {"payload": {"status": "passed", "detail": "verified"}},
            },
        ),
        ("reload", {"state": "reloaded", "old_pid": 1, "new_pid": 2}),
        ("reload", {"state": "started", "new_pid": 2}),
        ("rollback", {"state": "unavailable", "detail": "no predecessor"}),
        ("recover", {"state": "not_required"}),
        ("uninstall", {"state": "not_installed", "stopped": 0, "command_removed": False}),
    ],
)
def test_other_commands_reject_undeclared_public_evidence(
    command: str, result: dict[str, object], *, mocker, capsys
) -> None:
    marker = "private-evidence-marker"
    mocker.patch.object(application, "dispatch", return_value={**result, "undeclared": marker})

    assert application._execute(command, as_json=True) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert marker not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "internal_error"


def test_doctor_rejects_undeclared_check_evidence(*, mocker, capsys) -> None:
    marker = "private-evidence-marker"
    report = {
        "state": "running",
        "ok": True,
        "next": None,
        "checks": {"payload": {"status": "passed", "detail": "verified", "undeclared": marker}},
    }
    mocker.patch.object(application, "dispatch", return_value=report)

    assert application._execute("doctor", as_json=True) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert marker not in captured.err
    assert json.loads(captured.err)["error"]["code"] == "internal_error"


def test_doctor_rejects_mistyped_check_status() -> None:
    with pytest.raises(ValueError, match="doctor result is invalid"):
        application.outcome.admit(
            "doctor",
            {
                "state": "degraded",
                "ok": False,
                "next": "openai-responses-proxy status --json",
                "checks": {"payload": {"status": ["failed"], "detail": "invalid"}},
            },
        )


@pytest.mark.parametrize(
    ("check", "expected_exit"),
    [("command", 0), ("payload", 1), ("rollback", 1)],
)
def test_doctor_json_reports_categories_without_private_paths(
    check: str, expected_exit: int, *, mocker
) -> None:
    private = "/private/operator/recovery"
    status: dict[str, object] = {
        "state": "running",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "runtime": {"pid": 321, "accepting": True},
        "command": {"state": "owned", "path": private},
        "payload_transaction": None,
        "rollback": {"state": "available"},
    }
    if check == "payload":
        status["state"] = "degraded"
        status["payload_integrity"] = {"ok": False, "detail": f"manifest at {private} is invalid"}
    elif check == "rollback":
        status["state"] = "invalid"
        status["rollback"] = {"state": "invalid", "detail": f"snapshot {private} is invalid"}
    mocker.patch.object(application.control, "status", return_value=status)

    code, stdout, stderr = invoke("doctor", "--json")

    assert code == expected_exit
    assert stderr == ""
    assert private not in stdout
    report = json.loads(stdout)
    assert report["checks"][check]["status"] == ("passed" if expected_exit == 0 else "failed")


@pytest.mark.parametrize(
    ("source_state", "public_state"),
    [("degraded", "degraded"), ("invalid", "invalid"), ("unexpected", "invalid")],
)
def test_doctor_preserves_lifecycle_failure_when_component_checks_pass(
    source_state: str, public_state: str, *, mocker
) -> None:
    private = "/private/operator/installation"
    status: dict[str, object] = {
        "state": source_state,
        "detail": f"installed receipt at {private} does not match selected payload",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "runtime": {"pid": 321, "accepting": True},
        "command": {"state": "owned", "path": private},
        "payload_transaction": None,
        "rollback": {"state": "available"},
    }
    mocker.patch.object(application.control, "status", return_value=status)

    code, stdout, stderr = invoke("doctor", "--json")

    assert code == 1
    assert stderr == ""
    assert private not in stdout
    report = json.loads(stdout)
    assert report["state"] == public_state
    assert report["ok"] is False
    assert report["checks"]["installation"] == {"status": "failed", "detail": public_state}
    assert report["next"] == application.product_identity.command("status", "--json")

    human_code, human_stdout, human_stderr = invoke("doctor")

    assert human_code == code
    assert human_stderr == ""
    assert report["next"] in human_stdout
    assert private not in human_stdout


@pytest.mark.parametrize(
    "override",
    [
        {"listener_pids": ["/private/operator"]},
        {"payload_integrity": {"ok": "true", "detail": "verified"}},
        {"command": {"state": 1}},
        {"command": {"state": "owned", "kind": 1, "path": "/bin/proxy"}},
        {"release": 405},
        {"runtime": []},
        {"payload_transaction": []},
    ],
)
def test_status_rejects_mistyped_nested_evidence(
    override: dict[str, object], *, mocker, capsys
) -> None:
    status: dict[str, object] = {
        "state": "running",
        "detail": "healthy",
        "release": "4.0.5",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "command": {"state": "owned", "kind": "symlink", "path": "/bin/proxy"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321},
        "payload_transaction": None,
    }
    status.update(override)

    with pytest.raises(ValueError, match="status result is invalid"):
        application.outcome.admit("status", status)
    mocker.patch.object(application, "dispatch", return_value=status)

    assert application._execute("status") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Action required" in captured.err
    assert "/private/operator" not in captured.err


def test_status_human_output_is_aligned_and_not_serialized_json(*, mocker) -> None:
    evidence = {
        "state": "running",
        "detail": "healthy",
        "release": "2.0.8",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321, "accepting": True},
        "payload_transaction": None,
        "command": {
            "path": "/commands/openai-responses-proxy",
            "state": "owned",
            "kind": "symlink",
        },
    }
    mocker.patch.object(application.control, "status", return_value=evidence)

    code, stdout, stderr = invoke("status")

    assert code == 0
    assert stderr == ""
    assert "OpenAI Responses Proxy  Status" in stdout
    assert "Release" in stdout
    assert "2.0.8" in stdout
    assert "Payload" in stdout
    assert "Verified" in stdout
    assert "Service" in stdout
    assert "Running" in stdout
    assert "Listener" in stdout
    assert "PID 321" in stdout
    assert "Command" in stdout
    assert "Owned" in stdout
    assert not stdout.lstrip().startswith("{")
    lines = stdout.splitlines()
    value_columns = {
        line.index(value)
        for line, value in (
            (next(line for line in lines if "2.0.8" in line), "2.0.8"),
            (next(line for line in lines if "Verified" in line), "Verified"),
            (next(line for line in lines if "Running" in line), "Running"),
        )
    }
    assert len(value_columns) == 1


def test_status_human_next_action_follows_the_lifecycle_state() -> None:
    base = {
        "release": "2.0.58",
        "payload_integrity": {"ok": True, "detail": "verified"},
        "service": "running",
        "listener_pids": [321],
        "runtime": {"pid": 321, "accepting": True},
        "command": {"state": "owned", "kind": "symlink"},
    }

    invalid = application.presentation.render(
        "status",
        {
            **base,
            "state": "invalid",
            "payload_transaction": {
                "state": "invalid",
                "detail": "payload transaction journal is missing",
            },
        },
    )
    recoverable = application.presentation.render(
        "status",
        {
            **base,
            "state": "recovery_required",
            "payload_transaction": {"state": "committed"},
        },
    )

    assert "openai-responses-proxy status --json" in invalid
    assert "openai-responses-proxy recover" not in invalid
    assert "openai-responses-proxy recover" in recoverable


def test_human_projection_covers_degraded_and_complete_command_results() -> None:
    degraded = application.presentation.render(
        "status",
        {
            "release": "",
            "payload_integrity": {"ok": False},
            "service": "",
            "listener_pids": [],
        },
    )
    assert "Not installed" in degraded
    assert "Action required" in degraded
    assert "openai-responses-proxy doctor" in degraded

    doctor = application.presentation.render(
        "doctor",
        {
            "next": "openai-responses-proxy reload",
            "checks": {
                "payload": {"status": "passed"},
                "listener": {
                    "status": "failed",
                },
                "ignored": "not a check",
            },
        },
    )
    assert "Payload" in doctor
    assert "Passed" in doctor
    assert "Listener" in doctor
    assert "Action required" in doctor
    assert "openai-responses-proxy reload" in doctor

    installed = application.presentation.render("install", {"runtime": {"release": "2.0.11"}})
    assert "Installed" in installed
    assert "2.0.11" in installed
    upgraded = application.presentation.render(
        "install", {"state": "upgraded", "runtime": {"release": "2.0.12"}}
    )
    assert "Upgraded" in upgraded
    assert "2.0.12" in upgraded
    assert "Reloaded" in application.presentation.render("reload", {"old_pid": 1, "new_pid": 2})
    assert "Rolled Back" in application.presentation.render(
        "recover", {"version": "2.0.11", "state": "rolled_back"}
    )
    assert "Finalized" in application.presentation.render(
        "recover", {"version": "2.0.12", "state": "finalized"}
    )
    assert "No recovery required" in application.presentation.render(
        "recover", {"state": "not_required"}
    )
    closed = application.presentation.render(
        "recover",
        {"transaction_id": "tx-closed", "version": "2.0.12", "state": "closed"},
    )
    assert "Closed" in closed
    assert "Transaction tx-closed" in closed
    assert "Release     2.0.12" in closed
    assert "Not installed" in application.presentation.render(
        "uninstall",
        {
            "state": "not_installed",
            "stopped": 0,
            "command_removed": False,
        },
    )
    assert "Purged" in application.presentation.render(
        "uninstall", {"state": "purged", "stopped": 1, "command_removed": True}
    )
    assert application.presentation.render("future", {}) == ""


def test_status_human_output_exposes_the_classification_detail() -> None:
    rendered = application.presentation.render(
        "status",
        {
            "state": "invalid",
            "detail": "payload transaction journal is missing",
            "release": "2.0.58",
            "payload_integrity": {"ok": True, "detail": "verified"},
            "service": "running",
            "listener_pids": [321],
            "runtime": None,
            "payload_transaction": {
                "state": "invalid",
                "detail": "payload transaction journal is missing",
            },
            "command": {"state": "owned", "kind": "symlink"},
        },
    )

    assert "payload transaction journal is missing" in rendered


def test_rollback_without_a_predecessor_has_matching_human_and_json_semantics(
    tmp_path: Path, *, mocker
) -> None:
    unavailable = {
        "state": "unavailable",
        "detail": "no verified predecessor is retained",
    }
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "rollback", return_value=unavailable)

    json_code, json_stdout, json_stderr = invoke("rollback", "--to-release", "3.0.5", "--json")
    human_code, human_stdout, human_stderr = invoke("rollback", "--to-release", "3.0.5")

    assert json_code == human_code == 0
    assert json.loads(json_stdout) == {**unavailable, "next": "openai-responses-proxy status"}
    assert "No verified predecessor" in human_stdout
    assert human_stdout.endswith(f"Next\n  {json.loads(json_stdout)['next']}\n")
    assert json_stderr == human_stderr == ""


def test_expected_lifecycle_failures_are_rendered_once(*, mocker) -> None:
    mocker.patch.object(
        application.runtime_context,
        "create",
        side_effect=errors.InstallError(
            "bad port",
            next_command="openai-responses-proxy status --help",
        ),
    )
    code, stdout, stderr = invoke("status", "--json")
    assert code == 2
    assert stdout == ""
    assert json.loads(stderr) == {
        "error": {
            "code": "lifecycle_error",
            "message": "bad port",
            "next": "openai-responses-proxy status --help",
        }
    }


def test_invalid_result_and_unknown_dispatch_have_explicit_boundaries() -> None:
    with pytest.raises(ValueError, match="not an object"):
        application.outcome.admit("status", None)

    with pytest.raises(ValueError, match="not implemented"):
        application.dispatch("future", port=8792)
