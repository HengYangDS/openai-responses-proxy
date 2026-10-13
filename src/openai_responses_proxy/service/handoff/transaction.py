"""Protocol-v2 rolling handoff for OpenAI Responses Proxy.

This module owns the process-local transaction, listener transfer, parent/child
control channel, and bounded rollback semantics. The proxy entrypoint supplies
its drain gate, server construction, runtime identity, and logging primitives
through :class:`Context`.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict

from openai_responses_proxy.service import identity
from openai_responses_proxy.service.handoff.protocol import HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS
from openai_responses_proxy.service.handoff.protocol import HANDOFF_PROTOCOL_VERSION
from openai_responses_proxy.service.handoff.protocol import HandoffChild
from openai_responses_proxy.service.handoff.protocol import HandoffError
from openai_responses_proxy.service.handoff.protocol import JsonObject
from openai_responses_proxy.service.handoff.protocol import ReadOnlyJsonObject
from openai_responses_proxy.service.handoff.protocol import listener_from_prepare
from openai_responses_proxy.service.handoff.protocol import probe_health
from openai_responses_proxy.service.handoff.protocol import (
    read_control_message as _read_control_message,
)
from openai_responses_proxy.service.handoff.protocol import spawn_child
from openai_responses_proxy.service.handoff.protocol import (
    write_control_message as _write_control_message,
)


class PreparedHandoff(TypedDict):
    """Validated replacement and bounded timing used at the commit barrier."""

    child: HandoffChild
    expected: JsonObject
    timeout_seconds: float
    lease_seconds: float


HANDOFF_READY_TIMEOUT_SECONDS = 30.0
HANDOFF_DEFAULT_LEASE_SECONDS = 30.0
SELECTED_GENERATION_HANDOFF_CAPABILITY = "selected-generation-handoff"
ADMISSION_PRESERVING_HANDOFF_CAPABILITY = "admission-preserving-handoff"

_IDENTITY_FIELDS = (
    "transaction_id",
    "release",
    "serving_payload_sha256",
    "release_receipt_sha256",
    "manifest_sha256",
)


class HandoffConflictError(HandoffError):
    """Another process-local handoff already owns the single-flight session."""


@dataclass(frozen=True)
class Context:
    """Proxy-owned primitives needed by the handoff transaction."""

    executable: Path
    state_root: Path
    successor_executable: Callable[[], Path]
    release_version: Callable[[], str]
    serving_payload_sha256: Callable[[], str | None]
    release_receipt_sha256: Callable[[], str | None]
    payload_manifest_sha256: Callable[[], str | None]
    committed_payload: Callable[[], identity.LoadedPayloadIdentity | None]
    response_gate_lock: threading.Lock
    draining: Callable[[], bool]
    active_responses: Callable[[], int]
    active_handlers: Callable[[], int]
    log: Callable[[str], None]
    server_factory: Callable[[socket.socket], ThreadingHTTPServer]
    set_server_instance: Callable[[ThreadingHTTPServer], None]


_HANDOFF_TRANSITIONS = {
    "idle": frozenset(("preparing",)),
    "preparing": frozenset(("ready", "aborting")),
    "ready": frozenset(("committing", "aborting")),
    "committing": frozenset(("serving", "aborting")),
    "serving": frozenset(("finalizing", "aborting")),
    "finalizing": frozenset(("finalized", "aborting")),
    "finalized": frozenset(("idle",)),
    "aborting": frozenset(("rolled_back",)),
    "rolled_back": frozenset(("idle",)),
}
_HANDOFF_LOCK = threading.RLock()
_HANDOFF_SESSION: dict[str, object] = {}


def validate_transition(current_state: str, target_state: str) -> bool:
    """Return whether one explicit protocol-v2 state transition is legal."""
    return target_state in _HANDOFF_TRANSITIONS.get(current_state, frozenset())


def reset_session_to_idle() -> None:
    """Reset the process-local transaction only after child cleanup is complete."""
    with _HANDOFF_LOCK:
        _HANDOFF_SESSION.clear()
        _HANDOFF_SESSION.update(
            state="idle",
            transaction_id=None,
            child_pid=None,
            outcome=None,
            outcome_ready=threading.Event(),
            lease_seconds=HANDOFF_DEFAULT_LEASE_SECONDS,
            drain_deadline=None,
        )


def _transition(target_state: str) -> None:
    with _HANDOFF_LOCK:
        current = str(_HANDOFF_SESSION.get("state", "idle"))
        if not validate_transition(current, target_state):
            raise HandoffError(f"illegal handoff transition {current}->{target_state}")
        _HANDOFF_SESSION["state"] = target_state


def disk_payload_matches_expected(expected: ReadOnlyJsonObject, context: Context) -> bool:
    """Verify the payload that a replacement child would load from disk."""
    committed = context.committed_payload()
    return (
        committed is not None
        and {key: expected.get(key) for key in committed.handoff()} == committed.handoff()
    )


def runtime_identity(context: Context) -> dict[str, object]:
    """Return secret-free process and transaction identity for health proofs."""
    with _HANDOFF_LOCK, context.response_gate_lock:
        state = str(_HANDOFF_SESSION.get("state", "idle"))
        accepting = not context.draining()
        return {
            "pid": os.getpid(),
            "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
            "handoff_capabilities": [
                SELECTED_GENERATION_HANDOFF_CAPABILITY,
                ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
            ],
            "handoff_transaction_id": _HANDOFF_SESSION.get("transaction_id"),
            "handoff_state": state,
            "payload_manifest_sha256": context.payload_manifest_sha256(),
            "accepting": accepting,
            "active_handlers": context.active_handlers(),
        }


def _control_message(
    message_type: str, expected: ReadOnlyJsonObject, *, include_identity: bool = False
) -> JsonObject:
    message = {
        "type": message_type,
        "pid": expected["pid"],
        "transaction_id": expected["transaction_id"],
    }
    if include_identity:
        message.update(
            protocol_version=HANDOFF_PROTOCOL_VERSION,
            release=expected["release"],
            serving_payload_sha256=expected["serving_payload_sha256"],
            release_receipt_sha256=expected["release_receipt_sha256"],
            manifest_sha256=expected["manifest_sha256"],
        )
    return message


def _expected_serving_health(expected: ReadOnlyJsonObject) -> JsonObject:
    """Return the exact network-visible identity required before finalization."""
    return {
        "pid": expected["pid"],
        "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
        "handoff_transaction_id": expected["transaction_id"],
        "release": expected["release"],
        "serving_payload_sha256": expected["serving_payload_sha256"],
        "release_receipt_sha256": expected["release_receipt_sha256"],
        "payload_manifest_sha256": expected["manifest_sha256"],
        "handoff_state": "serving",
        "accepting": True,
        "draining": False,
    }


def _expect_child_phase(
    child: HandoffChild,
    expected: ReadOnlyJsonObject,
    phase: str,
    timeout_seconds: float,
) -> None:
    """Require the replacement child to echo one exact protocol phase identity."""
    message = _control_message(phase, expected, include_identity=phase == "ready")
    if child.recv_message(timeout_seconds) != message:
        raise HandoffError(f"handoff child {phase.upper()} identity mismatch")


def _reset_preparation(child: HandoffChild | None) -> None:
    """Release preparation state only after confirming an owned child has exited."""
    try:
        if child is not None:
            abort(child)
    finally:
        with _HANDOFF_LOCK:
            if child is None or _HANDOFF_SESSION.get("state") == "rolled_back":
                _HANDOFF_SESSION["state"] = "idle"
            _HANDOFF_SESSION.update(transaction_id=None, child_pid=None)


def _ignore_failure(action: Callable[[], object]) -> None:
    try:
        action()
    except Exception:
        pass


def abort(child: HandoffChild) -> None:
    """Abort one child and confirm its exit before exposing rollback."""
    with _HANDOFF_LOCK:
        state = _HANDOFF_SESSION.get("state")
        if state not in {"rolled_back", "idle"}:
            _HANDOFF_SESSION["state"] = "aborting"
    _ignore_failure(lambda: child.send_message({"type": "abort"}))
    stopped = False
    for stop in (child.terminate_bounded, child.kill_bounded):
        try:
            stopped = stop(HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS)
        except Exception:
            continue
        if stopped:
            break
    if not stopped:
        raise HandoffError("handoff child could not be confirmed exited")
    with _HANDOFF_LOCK:
        if state not in {"rolled_back", "idle"}:
            _HANDOFF_SESSION["state"] = "rolled_back"


def prepare(
    server: ThreadingHTTPServer,
    expected: ReadOnlyJsonObject,
    context: Context,
    *,
    timeout_seconds: float = HANDOFF_READY_TIMEOUT_SECONDS,
    lease_seconds: float = HANDOFF_DEFAULT_LEASE_SECONDS,
) -> PreparedHandoff:
    """Prepare and validate a non-accepting replacement without closing admission."""
    required = _IDENTITY_FIELDS
    if not isinstance(expected, dict) or any(
        not isinstance(expected.get(key), str) or not expected.get(key) for key in required
    ):
        raise HandoffError("handoff request identity is incomplete")
    with _HANDOFF_LOCK:
        if _HANDOFF_SESSION.get("state") == "finalized":
            reset_session_to_idle()
        if _HANDOFF_SESSION.get("state") != "idle":
            raise HandoffConflictError("handoff is already in progress")
        _transition("preparing")
        _HANDOFF_SESSION.update(
            transaction_id=expected["transaction_id"],
            child_pid=None,
            outcome=None,
            outcome_ready=threading.Event(),
            lease_seconds=lease_seconds,
            timeout_seconds=timeout_seconds,
        )
    child = None
    try:
        child_context = replace(context, executable=context.successor_executable())
        child = spawn_child(
            server.socket,
            expected,
            child_context,
            startup_timeout_seconds=timeout_seconds,
        )
        child_expected = {**expected, "pid": child.runtime_pid}
        _expect_child_phase(child, child_expected, "ready", timeout_seconds)
        with _HANDOFF_LOCK:
            _HANDOFF_SESSION.update(
                child_pid=child.runtime_pid, child=child, expected=dict(expected)
            )
            _transition("ready")
        return {
            "child": child,
            "expected": dict(expected),
            "timeout_seconds": timeout_seconds,
            "lease_seconds": lease_seconds,
        }
    except Exception as exc:
        _reset_preparation(child)
        if isinstance(exc, HandoffError):
            raise
        raise HandoffError("handoff child preparation failed") from exc


def _set_outcome(outcome: str) -> None:
    with _HANDOFF_LOCK:
        _HANDOFF_SESSION["outcome"] = outcome
        event = _HANDOFF_SESSION.get("outcome_ready")
        if isinstance(event, threading.Event):
            event.set()


def commit(server: ThreadingHTTPServer, prepared: PreparedHandoff, context: Context) -> str:
    """Cross the accept barrier and either finalize or expose a resumable rollback."""
    child = prepared["child"]
    expected = prepared["expected"]
    child_expected = {**expected, "pid": child.runtime_pid}
    timeout_seconds = prepared["timeout_seconds"]
    accept_stopped = False
    phase = "shutdown"
    try:
        _transition("committing")
        with _HANDOFF_LOCK:
            _HANDOFF_SESSION["drain_deadline"] = time.monotonic() + prepared["lease_seconds"]
        server.shutdown()
        accept_stopped = True
        phase = "serving"
        child.send_message({"type": "commit"})
        _expect_child_phase(child, child_expected, "serving", timeout_seconds)
        _transition("serving")
        phase = "finalize"
        _transition("finalizing")
        child.send_message({"type": "finalize"})
        _expect_child_phase(child, child_expected, "finalized", timeout_seconds)
        _transition("finalized")
        _set_outcome("finalized")
        return "finalized"
    except Exception as exc:
        context.log(f"event=handoff_commit_failed phase={phase} exception={exc.__class__.__name__}")
        try:
            abort(child)
            outcome = "rolled_back"
        except Exception:
            outcome = "abort_unconfirmed"
        _set_outcome(outcome)
        if not accept_stopped:
            _ignore_failure(server.shutdown)
        return outcome


def _serve_until_stopped(
    server: ThreadingHTTPServer, initial_serving_thread: threading.Thread | None
) -> None:
    if initial_serving_thread is None:
        server.serve_forever()
    else:
        initial_serving_thread.join()


def _wait_for_handoff_outcome(context: Context) -> tuple[object, object]:
    with _HANDOFF_LOCK:
        state = str(_HANDOFF_SESSION.get("state", "idle"))
        outcome = _HANDOFF_SESSION.get("outcome")
        outcome_ready = _HANDOFF_SESSION.get("outcome_ready")
        raw_timeout = _HANDOFF_SESSION.get("timeout_seconds", 1.0)
    if outcome is None and state == "idle":
        with context.response_gate_lock:
            if not context.draining():
                return outcome, None
    timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float)) else 1.0
    if isinstance(outcome_ready, threading.Event) and not outcome_ready.is_set():
        outcome_ready.wait(timeout=max(1.0, timeout * 3 + HANDOFF_CHILD_EXIT_TIMEOUT_SECONDS))
    with _HANDOFF_LOCK:
        return _HANDOFF_SESSION.get("outcome"), _HANDOFF_SESSION.get("drain_deadline")


def _finish_old_listener(context: Context, deadline: object) -> None:
    drain_deadline = deadline if isinstance(deadline, (int, float)) else time.monotonic()
    with context.response_gate_lock:
        active = max(context.active_responses(), context.active_handlers())
    while active > 0 and time.monotonic() < drain_deadline:
        time.sleep(0.05)
        with context.response_gate_lock:
            active = max(context.active_responses(), context.active_handlers())
    if active > 0:
        context.log(f"event=handoff_old_drain_expired remaining_active={active}")


def serve_with_resume(
    server: ThreadingHTTPServer,
    context: Context,
    *,
    initial_serving_thread: threading.Thread | None = None,
) -> None:
    """Serve until ordinary stop, finalized replacement, or a resumable rollback."""
    _serve_until_stopped(server, initial_serving_thread)
    while True:
        outcome, deadline = _wait_for_handoff_outcome(context)
        if outcome != "rolled_back":
            break
        reset_session_to_idle()
        server.serve_forever()
    if outcome == "finalized":
        _finish_old_listener(context, deadline)
    elif outcome == "abort_unconfirmed":
        context.log("event=handoff_abort_unconfirmed action=old_listener_exit")
    elif outcome is not None:
        context.log("event=handoff_outcome_unconfirmed action=old_listener_exit")


def _valid_prepare(message: object, context: Context) -> bool:
    if not isinstance(message, dict):
        return False
    listener_field = "listener_share_b64" if "listener_share_b64" in message else "listener_fd"
    expected = {
        "type": "prepare",
        "protocol_version": HANDOFF_PROTOCOL_VERSION,
        "release": context.release_version(),
        "serving_payload_sha256": context.serving_payload_sha256(),
        "release_receipt_sha256": context.release_receipt_sha256(),
        "manifest_sha256": context.payload_manifest_sha256(),
        listener_field: message.get(listener_field),
    }
    transaction_id = message.get("transaction_id")
    return (
        isinstance(transaction_id, str)
        and bool(transaction_id)
        and message == {**expected, "transaction_id": transaction_id}
    )


def run_child(context: Context) -> int:
    """Hold the inherited listener dormant until the parent crosses COMMIT."""
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    server = None
    serving_thread = None
    try:
        _write_control_message(output_stream, {"type": "started", "pid": os.getpid()})
        prepare_message = _read_control_message(input_stream)
        if not _valid_prepare(prepare_message, context):
            raise HandoffError("handoff PREPARE identity mismatch")
        _transition("preparing")
        with _HANDOFF_LOCK:
            _HANDOFF_SESSION.update(
                transaction_id=prepare_message["transaction_id"],
                child_pid=os.getpid(),
                expected={key: prepare_message[key] for key in _IDENTITY_FIELDS},
            )
        listener = listener_from_prepare(prepare_message)
        server = context.server_factory(listener)
        context.set_server_instance(server)
        _transition("ready")
        child_expected = {**prepare_message, "pid": os.getpid()}
        _write_control_message(
            output_stream,
            _control_message("ready", child_expected, include_identity=True),
        )
        command = _read_control_message(input_stream)
        if command != {"type": "commit"}:
            raise HandoffError("handoff child did not receive COMMIT")
        _transition("committing")
        _transition("serving")
        serving_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="responses-proxy-handoff-serving",
        )
        serving_thread.start()
        address: object = getattr(server, "server_address", None)
        if not isinstance(address, (tuple, list)) or len(address) <= 1:
            raise HandoffError("handoff server has no probeable listener address")
        health_port = address[1]
        if not isinstance(health_port, int) or not 1 <= health_port <= 65535:
            raise HandoffError("handoff server has no valid listener port")
        probe_health(
            health_port,
            timeout_seconds=HANDOFF_READY_TIMEOUT_SECONDS,
            expected=_expected_serving_health(child_expected),
        )
        _write_control_message(
            output_stream,
            _control_message("serving", child_expected),
        )
        command = _read_control_message(input_stream)
        if command == {"type": "abort"}:
            raise HandoffError("handoff parent aborted before FINALIZE")
        if command != {"type": "finalize"}:
            raise HandoffError("handoff child did not receive FINALIZE")
        _transition("finalizing")
        _transition("finalized")
        _write_control_message(output_stream, _control_message("finalized", child_expected))
        serve_with_resume(server, context, initial_serving_thread=serving_thread)
        return 0
    except Exception:
        if server is not None:
            if serving_thread is not None:
                _ignore_failure(server.shutdown)
                serving_thread.join(timeout=2)
            _ignore_failure(server.server_close)
        return 1


reset_session_to_idle()
