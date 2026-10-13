"""Admit one public command result before human or JSON projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from typing import cast

from openai_responses_proxy import json_value
from openai_responses_proxy import product_identity

type PublicCommand = Literal[
    "install", "status", "doctor", "recover", "reload", "rollback", "uninstall"
]
type SuccessState = Literal[
    "installed",
    "upgraded",
    "unchanged",
    "running",
    "degraded",
    "stopped",
    "invalid",
    "not_installed",
    "not_required",
    "recovery_required",
    "reloaded",
    "started",
    "unavailable",
    "rolled_back",
    "closed",
    "finalized",
    "purged",
    "uninstalled",
]

_STATUS_NEXT = {
    "not_installed": ("install", "--help"),
    "invalid": ("status", "--json"),
    "recovery_required": ("recover",),
    "stopped": ("reload",),
    "degraded": ("doctor",),
}


@dataclass(frozen=True, slots=True)
class Success:
    """One validated successful command observation or mutation."""

    command: PublicCommand
    state: SuccessState
    evidence: dict[str, object]
    exit_code: int
    next_command: str | None


@dataclass(frozen=True, slots=True)
class Failure:
    """One bounded public failure independent of a private exception."""

    code: str
    problem: str
    next_command: str
    exit_code: Literal[2] = 2


type PublicOutcome = Success | Failure


def next_action(command: str, evidence: Mapping[str, object]) -> str | None:
    """Return the one safe follow-up command that a successful result names."""
    state = evidence.get("state")
    if command == "status":
        arguments = _STATUS_NEXT.get(str(state or "degraded"))
        return product_identity.command(*arguments) if arguments else None
    if command == "doctor":
        named = evidence.get("next")
        return named if isinstance(named, str) else None
    if command in {"install", "reload", "rollback"} or (
        command == "recover" and state != "not_required"
    ):
        return product_identity.command("status")
    return None


def _valid_doctor_checks(checks: Mapping[str, object]) -> bool:
    """Admit only named, categorical diagnostics to the public result."""
    names = {"installation", "payload", "service", "listener", "command", "transaction", "rollback"}
    return (
        bool(checks)
        and set(checks) <= names
        and all(
            isinstance(check, Mapping)
            and set(check) == {"status", "detail"}
            and isinstance(check["status"], str)
            and check["status"] in {"passed", "failed"}
            and isinstance(check["detail"], str)
            for check in checks.values()
        )
    )


def admit(command: str, raw: Mapping[str, object] | None) -> Success:
    """Reject malformed internal results instead of presenting false success."""
    if not isinstance(raw, Mapping):
        raise ValueError("public command result is not an object")
    evidence = dict(raw)
    if not json_value.is_json_object(evidence):
        raise ValueError("public command result is not a finite JSON object")
    state = evidence.get("state")
    match command, evidence:
        case "status", {
            "state": (
                "running"
                | "degraded"
                | "stopped"
                | "invalid"
                | "not_installed"
                | "recovery_required"
            ),
            "detail": str(),
            "release": (str() | None),
            "payload_integrity": {"ok": bool(), "detail": str()} as integrity,
            "command": {
                "state": ("owned" | "absent" | "foreign"),
                "kind": (str() | None),
                "path": str() as command_path,
            } as command_state,
            "service": str(),
            "listener_pids": list() as listeners,
            "runtime": (dict() | None),
            "payload_transaction": (dict() | None),
            **rest,
        } if (
            command_path
            and all(type(pid) is int and pid > 0 for pid in listeners)
            and set(integrity) == {"ok", "detail"}
            and set(command_state) == {"state", "kind", "path"}
            and (
                not rest
                or (
                    set(rest) == {"rollback"}
                    and isinstance(rest["rollback"], dict)
                    and set(rest["rollback"]) <= {"state", "from_release", "to_release", "detail"}
                    and isinstance(rest["rollback"].get("state"), str)
                    and all(isinstance(value, str) for value in rest["rollback"].values())
                )
            )
        ):
            pass
        case "doctor", {
            "state": _,
            "ok": bool() as healthy,
            "next": (str() | None) as next_command,
            "checks": Mapping() as checks,
            **rest,
        } if (
            state
            in {"running", "degraded", "stopped", "invalid", "not_installed", "recovery_required"}
            and not rest
            and _valid_doctor_checks(checks)
            and healthy == all(check["status"] == "passed" for check in checks.values())
            and (next_command is None) == healthy
        ):
            pass
        case "install", {
            "state": ("installed" | "upgraded" | "unchanged"),
            "release": str(),
            **rest,
        } if not rest:
            pass
        case "install", {
            "state": ("installed" | "upgraded"),
            "runtime": {"release": str()},
            **rest,
        } if not rest:
            pass
        case "reload", {
            "state": "reloaded",
            "old_pid": old_pid,
            "new_pid": new_pid,
            **rest,
        } if (
            type(old_pid) is int
            and type(new_pid) is int
            and old_pid > 0
            and new_pid > 0
            and old_pid != new_pid
            and (
                not rest
                or (
                    set(rest) == {"transaction_id", "recovered_after_controller_failure"}
                    and isinstance(rest["transaction_id"], str)
                    and rest["recovered_after_controller_failure"] is True
                )
            )
        ):
            pass
        case "reload", {"state": "started", "new_pid": new_pid, **rest} if (
            type(new_pid) is int and new_pid > 0 and not rest
        ):
            pass
        case "rollback", {"state": "unavailable", "detail": str(), **rest} if not rest:
            pass
        case "rollback", {"state": "unchanged", "release": str(), **rest} if not rest:
            pass
        case "rollback", {
            "state": "rolled_back",
            "from_release": str(),
            "to_release": str(),
            **rest,
        } if not rest or (set(rest) == {"runtime"} and isinstance(rest["runtime"], dict)):
            pass
        case "recover", {"state": "not_required", **rest} if not rest:
            pass
        case "recover", {
            "state": ("closed" | "rolled_back" | "finalized" | "purged"),
            "transaction_id": str(),
            "version": str(),
            **rest,
        } if not rest or (
            set(rest) == {"stopped", "command_removed"}
            and type(rest["stopped"]) is int
            and rest["stopped"] >= 0
            and isinstance(rest["command_removed"], bool)
        ):
            pass
        case "uninstall", {
            "state": (
                "not_installed" | "uninstalled" | "purged" | "closed" | "rolled_back" | "finalized"
            ),
            "stopped": stopped,
            "command_removed": bool(),
            **rest,
        } if (
            type(stopped) is int
            and stopped >= 0
            and (
                not rest
                or (
                    set(rest) == {"transaction_id", "version"}
                    and isinstance(rest["transaction_id"], str)
                    and isinstance(rest["version"], str)
                )
            )
        ):
            pass
        case _:
            raise ValueError(f"{command} result is invalid")
    return Success(
        command,
        cast("SuccessState", state),
        evidence,
        1 if command == "doctor" and evidence["ok"] is False else 0,
        next_action(command, evidence),
    )
