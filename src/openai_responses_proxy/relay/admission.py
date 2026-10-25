"""Process-local handler, request-admission, and bounded drain state."""

from __future__ import annotations

import ipaddress
import threading
import time
import urllib.parse
from collections.abc import Buffer
from contextlib import suppress
from email.message import Message
from http.server import BaseHTTPRequestHandler
from typing import SupportsIndex
from typing import SupportsInt

from openai_responses_proxy.relay import telemetry

_MIN_DRAIN_LEASE_SECONDS = 1
_MAX_DRAIN_LEASE_SECONDS = 900
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_RESPONSE_GATE_LOCK = threading.Lock()
_ACTIVE_RESPONSES = 0
_ACTIVE_HANDLERS = 0
_DRAINING = False
_DRAIN_GENERATION = 0
_DRAIN_DEADLINE: float | None = None
_REQUEST_SEQUENCE = 0


def next_request_id() -> int:
    """Allocate one process-local request sequence number."""
    global _REQUEST_SEQUENCE
    with _RESPONSE_GATE_LOCK:
        _REQUEST_SEQUENCE += 1
        return _REQUEST_SEQUENCE


def bounded_drain_lease_seconds(value: object | None) -> int:
    """Return a bounded lease without making control startup fragile."""
    if isinstance(value, (str, Buffer, SupportsInt, SupportsIndex)):
        with suppress(TypeError, ValueError):
            return min(_MAX_DRAIN_LEASE_SECONDS, max(_MIN_DRAIN_LEASE_SECONDS, int(value)))
    return 30


def _expire_drain_locked() -> None:
    global _DRAINING, _DRAIN_GENERATION, _DRAIN_DEADLINE
    deadline = _DRAIN_DEADLINE or float("inf")
    if time.monotonic() >= deadline:
        _DRAINING = False
        _DRAIN_DEADLINE = None
        _DRAIN_GENERATION += 1
        telemetry.record_counter("drain_leases_expired")
        telemetry.record_failure("drain_lease_expired")


def _drain_lease_remaining_locked() -> int | None:
    if not _DRAINING or _DRAIN_DEADLINE is None:
        return None
    return max(0, int(_DRAIN_DEADLINE - time.monotonic() + 0.999))


def set_draining(enabled: bool, *, lease_seconds: object | None = None) -> dict[str, object]:
    """Atomically change local Responses admission and return its snapshot."""
    global _DRAINING, _DRAIN_GENERATION, _DRAIN_DEADLINE
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        previous = _DRAINING
        _DRAINING = bool(enabled)
        _DRAIN_GENERATION += previous != _DRAINING
        _DRAIN_DEADLINE = (
            time.monotonic() + bounded_drain_lease_seconds(lease_seconds) if enabled else None
        )
        return {
            "draining": _DRAINING,
            "drain_generation": _DRAIN_GENERATION,
            "active_responses": _ACTIVE_RESPONSES,
            "drain_lease_remaining_seconds": _drain_lease_remaining_locked(),
        }


def snapshot() -> dict[str, object]:
    """Return one admission-consistent runtime status projection."""
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        return {
            "active_responses": _ACTIVE_RESPONSES,
            "draining": _DRAINING,
            "drain_generation": _DRAIN_GENERATION,
            "drain_lease_remaining_seconds": _drain_lease_remaining_locked(),
        }


def drain_snapshot() -> tuple[bool, int, int]:
    """Return one admission-consistent drain and active-request snapshot."""
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        return _DRAINING, _DRAIN_GENERATION, _ACTIVE_RESPONSES


def response_gate_lock() -> threading.Lock:
    """Return the lock shared with handoff identity sampling."""
    return _RESPONSE_GATE_LOCK


def is_draining() -> bool:
    """Return the current drain latch while the caller holds the gate lock."""
    return _DRAINING


def active_responses() -> int:
    """Return the current active Responses count under the caller's gate lock."""
    return _ACTIVE_RESPONSES


def active_handlers() -> int:
    """Return the current HTTP handler count under the caller's gate lock."""
    return _ACTIVE_HANDLERS


def begin_handler() -> int:
    """Account for one HTTP handler and return the current total."""
    global _ACTIVE_HANDLERS
    with _RESPONSE_GATE_LOCK:
        _ACTIVE_HANDLERS += 1
        return _ACTIVE_HANDLERS


def end_handler() -> int:
    """Release one HTTP handler count and return the current total."""
    global _ACTIVE_HANDLERS
    with _RESPONSE_GATE_LOCK:
        _ACTIVE_HANDLERS = max(0, _ACTIVE_HANDLERS - 1)
        return _ACTIVE_HANDLERS


def admit_response() -> tuple[str, int]:
    """Admit one request unless lifecycle draining closes admission."""
    global _ACTIVE_RESPONSES
    with _RESPONSE_GATE_LOCK:
        _expire_drain_locked()
        if _DRAINING:
            return "draining", _ACTIVE_RESPONSES
        _ACTIVE_RESPONSES += 1
        return "acquired", _ACTIVE_RESPONSES


def release_response() -> int:
    """Release one active Responses count and return the current total."""
    global _ACTIVE_RESPONSES
    with _RESPONSE_GATE_LOCK:
        _ACTIVE_RESPONSES = max(0, _ACTIVE_RESPONSES - 1)
        return _ACTIVE_RESPONSES


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
    global \
        _ACTIVE_RESPONSES, \
        _ACTIVE_HANDLERS, \
        _DRAINING, \
        _DRAIN_GENERATION, \
        _DRAIN_DEADLINE, \
        _REQUEST_SEQUENCE
    with _RESPONSE_GATE_LOCK:
        _ACTIVE_RESPONSES = 0
        _ACTIVE_HANDLERS = 0
        _DRAINING = False
        _DRAIN_GENERATION = 0
        _DRAIN_DEADLINE = None
        _REQUEST_SEQUENCE = 0
