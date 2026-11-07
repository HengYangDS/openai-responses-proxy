"""Process-local handler, request-admission, and bounded drain state."""

from __future__ import annotations

import ipaddress
import threading
import time
import urllib.parse
from collections.abc import Buffer
from contextlib import suppress
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler
from typing import SupportsIndex
from typing import SupportsInt

from openai_responses_proxy.relay import telemetry

_MIN_DRAIN_LEASE_SECONDS = 1
_MAX_DRAIN_LEASE_SECONDS = 900
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_RESPONSE_GATE_LOCK = threading.Lock()


@dataclass(slots=True)
class _Admission:
    """Process-local admission counters and drain latch, guarded by the response gate lock."""

    active_responses: int = 0
    active_handlers: int = 0
    draining: bool = False
    drain_generation: int = 0
    drain_deadline: float | None = None
    request_sequence: int = 0


_STATE = _Admission()


def next_request_id() -> int:
    """Allocate one process-local request sequence number."""
    with _RESPONSE_GATE_LOCK:
        _STATE.request_sequence += 1
        return _STATE.request_sequence


def bounded_drain_lease_seconds(value: object | None) -> int:
    """Return a bounded lease without making control startup fragile."""
    if isinstance(value, (str, Buffer, SupportsInt, SupportsIndex)):
        with suppress(TypeError, ValueError):
            return min(_MAX_DRAIN_LEASE_SECONDS, max(_MIN_DRAIN_LEASE_SECONDS, int(value)))
    return 30


def _expire_drain_locked() -> None:
    deadline = _STATE.drain_deadline or float("inf")
    if time.monotonic() >= deadline:
        _STATE.draining = False
        _STATE.drain_deadline = None
        _STATE.drain_generation += 1
        telemetry.record_counter("drain_leases_expired")
        telemetry.record_failure("drain_lease_expired")


def _drain_lease_remaining_locked() -> int | None:
    if not _STATE.draining or _STATE.drain_deadline is None:
        return None
    return max(0, int(_STATE.drain_deadline - time.monotonic() + 0.999))


def set_draining(*, enabled: bool, lease_seconds: object | None = None) -> dict[str, object]:
    """Atomically change local Responses admission and return its snapshot."""
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        previous = _STATE.draining
        _STATE.draining = bool(enabled)
        _STATE.drain_generation += previous != _STATE.draining
        _STATE.drain_deadline = (
            time.monotonic() + bounded_drain_lease_seconds(lease_seconds) if enabled else None
        )
        return {
            "draining": _STATE.draining,
            "drain_generation": _STATE.drain_generation,
            "active_responses": _STATE.active_responses,
            "drain_lease_remaining_seconds": _drain_lease_remaining_locked(),
        }


def snapshot() -> dict[str, object]:
    """Return one admission-consistent runtime status projection."""
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        return {
            "active_responses": _STATE.active_responses,
            "draining": _STATE.draining,
            "drain_generation": _STATE.drain_generation,
            "drain_lease_remaining_seconds": _drain_lease_remaining_locked(),
        }


def response_gate_lock() -> threading.Lock:
    """Return the lock shared with handoff identity sampling."""
    return _RESPONSE_GATE_LOCK


def is_draining() -> bool:
    """Return the current drain latch while the caller holds the gate lock."""
    return _STATE.draining


def active_responses() -> int:
    """Return the current active Responses count under the caller's gate lock."""
    return _STATE.active_responses


def active_handlers() -> int:
    """Return the current HTTP handler count under the caller's gate lock."""
    return _STATE.active_handlers


def begin_handler() -> int:
    """Account for one HTTP handler and return the current total."""
    with _RESPONSE_GATE_LOCK:
        _STATE.active_handlers += 1
        return _STATE.active_handlers


def end_handler() -> int:
    """Release one HTTP handler count and return the current total."""
    with _RESPONSE_GATE_LOCK:
        _STATE.active_handlers = max(0, _STATE.active_handlers - 1)
        return _STATE.active_handlers


def admit_response() -> tuple[str, int]:
    """Admit one request unless lifecycle draining closes admission."""
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        if _STATE.draining:
            return "draining", _STATE.active_responses
        _STATE.active_responses += 1
        return "acquired", _STATE.active_responses


def release_response() -> int:
    """Release one active Responses count and return the current total."""
    with _RESPONSE_GATE_LOCK:
        _STATE.active_responses = max(0, _STATE.active_responses - 1)
        return _STATE.active_responses


def is_loopback_client(address: str) -> bool:
    """Require lifecycle control surfaces to remain local."""
    with suppress(ValueError):
        return ipaddress.ip_address(address).is_loopback
    return False


def is_local_controller(address: str, headers: Message) -> bool:
    """Admit lifecycle control only from a native loopback controller, never from a web page.

    A browser names the page's origin in `Origin` on a cross-origin request and keeps the page's
    host in `Host` even after DNS rebinding; the native lifecycle controller sends neither.
    """
    if not is_loopback_client(address) or headers.get("Origin") is not None:
        return False
    with suppress(ValueError):
        return urllib.parse.urlsplit(f"//{headers.get('Host', '')}").hostname in _LOOPBACK_HOSTS
    return False


def read_bounded_body(
    handler: BaseHTTPRequestHandler, length: int, timeout: float
) -> tuple[bytes | None, str]:
    """Spend one body deadline without reading beyond the declared frame."""
    deadline = time.monotonic() + timeout
    connection = handler.connection
    previous_timeout = connection.gettimeout()
    body = bytearray()
    try:
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "request_body_timeout"
            connection.settimeout(
                remaining if previous_timeout is None else min(previous_timeout, remaining)
            )
            chunk = handler.rfile.read1(min(length - len(body), 64 * 1024))
            if not chunk:
                return None, "incomplete_request_body"
            body.extend(chunk)
        if time.monotonic() >= deadline:
            return None, "request_body_timeout"
    except TimeoutError:
        return None, "request_body_timeout"
    except OSError:
        return None, "request_body_unreadable"
    finally:
        connection.settimeout(previous_timeout)
    return bytes(body), ""


def reset_for_test() -> None:
    """Reset admission state for deterministic unit tests."""
    with _RESPONSE_GATE_LOCK:
        _STATE.active_responses = 0
        _STATE.active_handlers = 0
        _STATE.draining = False
        _STATE.drain_generation = 0
        _STATE.drain_deadline = None
        _STATE.request_sequence = 0
