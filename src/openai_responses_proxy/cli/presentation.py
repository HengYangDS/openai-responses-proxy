"""Human-readable projections of the public command result model."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from collections.abc import Mapping
from itertools import starmap

from openai_responses_proxy import product_identity

_LABEL_WIDTH = 12
_RULE = "-" * 40


def _width(value: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1 for character in value
    )


def _row(label: str, value: object) -> str:
    padding = max(1, _LABEL_WIDTH - _width(label))
    return f"  {label}{' ' * padding}{value}"


def _page(
    title: str, rows: Iterable[tuple[str, object]], *, next_command: str | None = None
) -> str:
    lines = [f"{product_identity.DISPLAY_NAME}  {title}", _RULE, ""]
    lines.extend(starmap(_row, rows))
    if next_command:
        lines.extend(("", "Next", f"  {next_command}"))
    return "\n".join(lines)


def render(command: str, result: Mapping[str, object] | None) -> str:
    """Render one successful public command without exposing serialized internals."""
    if not isinstance(result, Mapping):
        return ""
    if command == "status":
        integrity = result.get("payload_integrity")
        payload_ok = isinstance(integrity, dict) and integrity.get("ok") is True
        listeners = result.get("listener_pids")
        listener = "Unavailable"
        if isinstance(listeners, list) and len(listeners) == 1:
            listener = f"PID {listeners[0]}"
        command_state = result.get("command")
        command_available = (
            isinstance(command_state, dict) and command_state.get("state") == "owned"
        )
        state = str(result.get("state") or "degraded")
        absent = state == "not_installed"
        transaction = result.get("payload_transaction")
        transaction_label = (
            str(transaction.get("state", "Invalid")).replace("_", " ").title()
            if isinstance(transaction, dict)
            else "None"
        )
        next_command = (
            product_identity.command("install", "--help")
            if absent
            else product_identity.command("status", "--json")
            if state == "invalid"
            else product_identity.command("recover")
            if state == "recovery_required"
            else product_identity.command("reload")
            if state == "stopped"
            else product_identity.command("doctor")
            if state == "degraded"
            else None
        )
        return _page(
            "Status",
            (
                ("State", state.replace("_", " ").title()),
                ("Detail", result.get("detail") or "Unavailable"),
                ("Release", result.get("release") or "Not installed"),
                (
                    "Payload",
                    "Verified" if payload_ok else "Absent" if absent else "Action required",
                ),
                (
                    "Command",
                    "Owned" if command_available else "Absent" if absent else "Action required",
                ),
                ("Service", str(result.get("service") or "Unknown").capitalize()),
                ("Listener", "Absent" if absent else listener),
                ("Transaction", transaction_label),
            ),
            next_command=next_command,
        )
    if command == "doctor":
        raw_checks = result.get("checks")
        checks = raw_checks if isinstance(raw_checks, Mapping) else {}
        rows = tuple(
            (
                str(name).capitalize(),
                "Passed" if check.get("status") == "passed" else "Action required",
            )
            for name, check in checks.items()
            if isinstance(check, Mapping)
        )
        raw_next = result.get("next")
        next_command = raw_next if isinstance(raw_next, str) else None
        return _page("Doctor", rows, next_command=next_command)
    if command == "install":
        raw_runtime = result.get("runtime")
        runtime = raw_runtime if isinstance(raw_runtime, Mapping) else {}
        release = result.get("release") or runtime.get("release") or "Verified release"
        return _page(
            {
                "upgraded": "Upgraded",
                "unchanged": "Already installed",
            }.get(str(result.get("state")), "Installed"),
            (("Release", release),),
            next_command=product_identity.command("status"),
        )
    if command == "reload":
        if result.get("state") == "started":
            return _page(
                "Started",
                (("Current PID", result.get("new_pid", "Unknown")),),
                next_command=product_identity.command("status"),
            )
        return _page(
            "Reloaded",
            (
                ("Previous PID", result.get("old_pid", "Unknown")),
                ("Current PID", result.get("new_pid", "Unknown")),
            ),
            next_command=product_identity.command("status"),
        )
    if command == "rollback":
        if result.get("state") == "unavailable":
            return _page(
                "Rollback unavailable",
                (("State", "No verified predecessor"),),
                next_command=product_identity.command("status"),
            )
        if result.get("state") == "unchanged":
            return _page(
                "Already selected",
                (("Release", result.get("release", "Unknown")),),
                next_command=product_identity.command("status"),
            )
        return _page(
            "Rolled back",
            (
                ("From", result.get("from_release", "Unknown")),
                ("To", result.get("to_release", "Unknown")),
            ),
            next_command=product_identity.command("status"),
        )
    if command == "recover":
        if result.get("state") == "not_required":
            return _page("No recovery required", (("State", "No pending transaction"),))
        state = str(result.get("state", "Unknown"))
        return _page(
            state.replace("_", " ").title(),
            (
                ("Transaction", result.get("transaction_id", "Unknown")),
                ("Release", result.get("version", "Unknown")),
            ),
            next_command=product_identity.command("status"),
        )
    if command == "uninstall":
        if result.get("state") == "not_installed":
            return _page("Not installed", (("State", "Nothing to remove"),))
        state = str(result.get("state", "uninstalled"))
        return _page(
            state.replace("_", " ").title(),
            (
                ("Stopped", result.get("stopped", 0)),
                ("Payload", "Removed" if state == "purged" else "Preserved"),
            ),
        )
    return ""


def error(message: str, *, next_command: str) -> str:
    """Render one bounded problem with a single safe next action."""
    return _page("Action required", (("Problem", message),), next_command=next_command)
