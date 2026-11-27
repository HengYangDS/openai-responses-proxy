"""Loopback lifecycle HTTP projections for OpenAI Responses Proxy.

This module owns only the HTTP representation of runtime status and protocol-v2
handoff preparation.  The handoff state machine remains owned by ``handoff`` and
process composition remains owned by the executable entrypoint.
"""

from __future__ import annotations

import json
import math
import threading
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import cast

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.serving.handoff import protocol as handoff_protocol
from openai_responses_proxy.serving.handoff import transaction as handoff

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer

_MIN_TIMEOUT_SECONDS = 0.1
_MAX_TIMEOUT_SECONDS = 120.0
_MIN_LEASE_SECONDS = 1.0
_MAX_LEASE_SECONDS = 900.0


@dataclass(frozen=True)
class Bindings:
    """Process-owned functions needed by the loopback control surface."""

    runtime_status: Callable[[], Mapping[str, object]]
    handoff_context: Callable[[], handoff.Context]


def _write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: Mapping[str, object],
) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def send_status(handler: BaseHTTPRequestHandler, bindings: Bindings) -> None:
    """Write the process-local, secret-free runtime status response."""
    _write_json(handler, 200, bindings.runtime_status())


def _bounded_seconds(value: object, *, minimum: float, maximum: float, default: float) -> float:
    """Bound one duration of a handoff request.

    A value that is no finite number takes the default.
    """
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        with suppress(ValueError, OverflowError):
            seconds = float(value)
            if math.isfinite(seconds):
                return min(maximum, max(minimum, seconds))
    return default


def _refusal(failure: handoff.HandoffError) -> tuple[int, str]:
    """Return the error status and the name with which a handoff that did not begin is refused."""
    if isinstance(failure, handoff.HandoffConflictError):
        return HTTPStatus.CONFLICT.value, "handoff_in_progress"
    if isinstance(failure, handoff.HandoffRequestExpiredError):
        return HTTPStatus.REQUEST_TIMEOUT.value, "handoff_request_expired"
    return HTTPStatus.SERVICE_UNAVAILABLE.value, "handoff_prepare_failed"


def prepare_handoff(
    handler: BaseHTTPRequestHandler, bindings: Bindings, *, accepted_at: float | None = None
) -> None:
    """Prepare one replacement and transfer COMMIT before READY projection.

    ``accepted_at`` is the listener's monotonic stamp of the accept of the request's
    connection. The handoff begins only within the request's timeout of it, and a request
    without a stamp is refused.
    """
    if not admission.is_local_client(handler.client_address[0], handler.headers):
        handler.send_error(403, "handoff control is available only to a local native controller")
        return
    try:
        content_length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError):
        handler.send_error(400, "invalid handoff content length")
        return
    if content_length <= 0 or content_length > handoff_protocol.HANDOFF_CONTROL_MAX_BYTES:
        handler.send_error(413, "handoff request exceeds the control limit")
        return
    raw, failure = admission.read_bounded_body(
        handler, content_length, handoff_protocol.HANDOFF_CONTROL_RECEIVE_TIMEOUT_SECONDS
    )
    if raw is None:
        # send_error closes the connection, so an unread remainder is never parsed as a request.
        if failure == "request_body_timeout":
            handler.send_error(408, "handoff request was not received in time")
        else:
            handler.send_error(400, "incomplete handoff request")
        return
    try:
        request = json.loads(raw)
    except (ValueError, RecursionError):
        # Beside malformed JSON and bytes: a number over the interpreter's digit limit
        # and a nesting past its depth. Each is a request that gets its refusal.
        handler.send_error(400, "invalid handoff JSON")
        return
    if not isinstance(request, dict):
        handler.send_error(400, "handoff request must be an object")
        return
    allowed = {
        "transaction_id",
        "release",
        "serving_payload_sha256",
        "release_receipt_sha256",
        "manifest_sha256",
        "timeout_seconds",
        "lease_seconds",
    }
    if set(request) - allowed:
        handler.send_error(400, "handoff request contains unknown fields")
        return
    expected = {
        key: request.get(key)
        for key in (
            "transaction_id",
            "release",
            "serving_payload_sha256",
            "release_receipt_sha256",
            "manifest_sha256",
        )
    }
    context = bindings.handoff_context()
    if not handoff.disk_payload_matches_expected(expected, context):
        handler.send_error(409, "handoff request does not match the current disk payload")
        return
    timeout_seconds = _bounded_seconds(
        request.get("timeout_seconds"),
        minimum=_MIN_TIMEOUT_SECONDS,
        maximum=_MAX_TIMEOUT_SECONDS,
        default=handoff.HANDOFF_READY_TIMEOUT_SECONDS,
    )
    lease_seconds = _bounded_seconds(
        request.get("lease_seconds"),
        minimum=_MIN_LEASE_SECONDS,
        maximum=_MAX_LEASE_SECONDS,
        default=handoff.HANDOFF_DEFAULT_LEASE_SECONDS,
    )
    try:
        server = cast("ThreadingHTTPServer", handler.server)
        prepared = handoff.prepare(
            server,
            expected,
            context,
            accepted_at=accepted_at,
            timeout_seconds=timeout_seconds,
            lease_seconds=lease_seconds,
        )
    except handoff.HandoffError as exc:
        status, error = _refusal(exc)
        _write_json(handler, status, {"ok": False, "error": error})
        return
    threading.Thread(
        target=handoff.commit,
        args=(server, prepared, context),
        daemon=True,
        name="responses-proxy-handoff-commit",
    ).start()
    try:
        _write_json(
            handler,
            202,
            {
                "ok": True,
                "state": "ready",
                "protocol_version": handoff.HANDOFF_PROTOCOL_VERSION,
                "child_pid": prepared["child"].runtime_pid,
                "transaction_id": expected["transaction_id"],
            },
        )
        handler.wfile.flush()
    except downstream.CLIENT_CLOSED:
        pass
