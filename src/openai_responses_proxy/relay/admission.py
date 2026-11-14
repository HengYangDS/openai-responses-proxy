"""Process-local handler and request-admission counts."""

from __future__ import annotations

import ipaddress
import threading
import time
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from email.message import Message
    from http.server import BaseHTTPRequestHandler

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_RESPONSE_GATE_LOCK = threading.Lock()


@dataclass(slots=True)
class _Admission:
    """Process-local admission counters, guarded by the response gate lock."""

    active_responses: int = 0
    active_handlers: int = 0
    request_sequence: int = 0


_STATE = _Admission()


def next_request_id() -> int:
    """Allocate one process-local request sequence number."""
    with _RESPONSE_GATE_LOCK:
        _STATE.request_sequence += 1
        return _STATE.request_sequence


def snapshot() -> dict[str, object]:
    """Return one admission-consistent runtime status projection."""
    with _RESPONSE_GATE_LOCK:
        return {"active_responses": _STATE.active_responses}


def response_gate_lock() -> threading.Lock:
    """Return the lock shared with handoff identity sampling."""
    return _RESPONSE_GATE_LOCK


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


def admit_response() -> int:
    """Count one admitted Responses request and return the current total."""
    with _RESPONSE_GATE_LOCK:
        _STATE.active_responses += 1
        return _STATE.active_responses


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
        _STATE.request_sequence = 0
