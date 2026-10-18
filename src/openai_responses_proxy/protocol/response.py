"""Validate live Responses payloads without changing their bytes.

Provider portability is enforced when a later request replays prior output.
The live response must retain encrypted control data until Codex has completed
the current turn's decryption and tool dispatch.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum

_JSON_TERMINALS = frozenset(("completed", "incomplete"))


class ValidationReason(StrEnum):
    """Fixed diagnostics owned by live response validation."""

    INVALID_EVENT = "invalid_responses_event"
    UNPROVED_AGENT_PLAINTEXT = "agent_delivery_plaintext_unproved"


class ResponseValidationError(ValueError):
    """A live response failed a named validation without carrying private data."""

    def __init__(self, reason: ValidationReason) -> None:
        """Bind one protocol-owned reason while retaining ValueError compatibility."""
        self.reason = reason
        super().__init__(reason.value)


def validation_reason(error: BaseException) -> str:
    """Report only a validation-owned fixed reason, never exception prose."""
    return error.reason.value if isinstance(error, ResponseValidationError) else "unknown"


def error_payload(
    message: str,
    error_type: str,
    code: str,
    *,
    reason: str | None = None,
    attempts: int | None = None,
) -> bytes:
    """Encode one stable local Responses error envelope."""
    error: dict[str, object] = {"message": message, "type": error_type, "code": code}
    if reason is not None:
        error["reason"] = reason
    if attempts is not None:
        error["attempts"] = attempts
    return json.dumps({"error": error}, separators=(",", ":")).encode()


def validate_sse_event(raw_event: bytes) -> bytes:
    """Validate JSON-bearing SSE data lines and return the exact event bytes."""
    sse_event_data(raw_event)
    return raw_event


def sse_event_data(raw_event: bytes) -> object:
    """Decode one SSE data field sequence without depending on JSON whitespace."""
    fields: list[bytes] = []
    for line in raw_event.splitlines():
        name, _, value = line.partition(b":")
        if name == b"data":
            fields.append(value.removeprefix(b" "))
    data = b"\n".join(fields)
    if not data or data == b"[DONE]":
        return None
    try:
        return json.loads(data)
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ResponseValidationError(ValidationReason.INVALID_EVENT) from exc


def failure_diagnostic(error: object) -> tuple[str, str]:
    """Extract a bounded failure class and UUID, never upstream prose or payloads."""
    if not isinstance(error, dict):
        return "unknown", "none"
    code = error.get("code")
    code = (
        str(code)
        if isinstance(code, str)
        and code in ("server_error", "rate_limit_exceeded", "invalid_prompt")
        else "unknown"
    )
    message = error.get("message")
    request_id = re.search(
        r"(?i)\brequest[ _-]id[ :]+([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\b",
        str(message)[:2048] if isinstance(message, str) else "",
    )
    return code, str(request_id[1]).lower() if request_id else "none"


def validate_json_response(raw_response: bytes) -> bytes:
    """Require a terminal Responses document and return its exact bytes."""
    try:
        payload = json.loads(raw_response)
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid_responses_success_body") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_responses_success_body")
    status = payload.get("status")
    if not isinstance(status, str) or status not in _JSON_TERMINALS:
        raise ValueError("invalid_responses_success_body")
    return raw_response
