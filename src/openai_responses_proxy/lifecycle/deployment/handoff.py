"""Controller-side transport and proof for protocol-v2 rolling handoff.

This module owns the loopback handoff request, successor identity checks, and
bounded convergence or rollback observations. Installed :mod:`control` invokes
it only for same-payload reload. Source-side
:mod:`openai_responses_proxy.lifecycle.deployment.apply` invokes it after a released
transaction commits a different admitted transaction.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import TypeGuard

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime import loopback
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles

HANDOFF_PROTOCOL_VERSION = 2
_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_MARGIN_SECONDS = 1.0
RUNTIME_DIGEST_FIELDS = [
    "serving_payload_sha256",
    "release_receipt_sha256",
    "payload_manifest_sha256",
]
_RUNTIME_IDENTITY_FIELDS = ["release", *RUNTIME_DIGEST_FIELDS]

RuntimeSnapshot = dict[str, object]
RuntimeReader = Callable[[runtime_context.RuntimeContext], RuntimeSnapshot | None]


class HandoffRefusedError(errors.InstallError):
    """The listener answered the handoff request with an error status.

    Its handler has then finished: the request began no handoff, or began one that failed,
    and it will begin none later. A request without that answer may still begin.
    """


def fields_match(actual: RuntimeSnapshot, expected: dict[str, object]) -> bool:
    """Return whether every expected runtime field holds its expected value."""
    return all(actual.get(field) == value for field, value in expected.items())


def positive_int(value: object) -> TypeGuard[int]:
    """Return whether a runtime value is a positive integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def capture_source_listener(
    ctx: runtime_context.RuntimeContext,
    runtime: RuntimeSnapshot,
) -> process.OwnedProcess:
    """Boundedly prove one runtime process owns admission before mutation."""
    pid = runtime.get("pid")
    if not positive_int(pid):
        raise errors.InstallError("installed runtime identity is not verified")
    owned = process.capture_executable(pid, ctx.executable, roles=serving_roles.LISTENER_ROLES)
    if owned is None:
        raise errors.InstallError("installed listener process generation is not verified")
    if not process.owned_process_alive(owned):
        raise errors.InstallError("installed listener process generation is not verified")
    return owned


def predecessor_lease_seconds(ctx: runtime_context.RuntimeContext, timeout_seconds: float) -> float:
    """Keep a handed-off predecessor serving until its accepted exchanges reach their deadline.

    Each accepted Responses exchange is bounded by the configured upstream timeout, so a
    shorter lease would end streams that are still within their own budget.
    """
    return max(1.0, timeout_seconds, ctx.upstream_timeout)


def _handoff_reply(request: urllib.request.Request, *, timeout_seconds: float) -> object:
    """Read the bounded JSON reply of an accepted handoff request, or raise one public error."""
    label = "handoff control"
    try:
        with loopback.open_request(request, timeout_seconds=timeout_seconds) as response:
            status = response.status
            raw = response.read(_MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise HandoffRefusedError(f"{label} returned HTTP {code}") from exc
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as exc:
        raise errors.InstallError(f"{label} is unavailable") from exc
    if status != HTTPStatus.ACCEPTED:
        raise errors.InstallError(f"{label} returned HTTP {status}")
    if len(raw) > _MAX_BODY_BYTES:
        raise errors.InstallError(f"{label} response is too large")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise errors.InstallError(f"{label} is unavailable") from exc


def _manifest_object(path: Path) -> dict[str, object]:
    """Read the payload manifest as one object with string keys."""
    with path.open(encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise TypeError("payload manifest must be an object with string keys")
    return {key: value for key, value in loaded.items() if isinstance(key, str)}


def expected_metadata(root: str) -> RuntimeSnapshot:
    """Read release, aggregate payload, receipt, and manifest identity."""
    try:
        manifest_path = Path(root) / inventory.MANIFEST_FILENAME
        manifest = _manifest_object(manifest_path)
        release = manifest["release"]
        serving_payload_sha256 = manifest["serving_payload_sha256"]
        release_receipt_sha256 = manifest["release_receipt_sha256"]
        manifest_sha256 = digest.sha256_file(manifest_path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise errors.InstallError(
            "payload files are unavailable for a handoff transaction"
        ) from exc
    if not isinstance(release, str) or not release:
        raise errors.InstallError("payload manifest has no release version")
    if not digest.is_sha256(serving_payload_sha256):
        raise errors.InstallError("payload manifest has no valid serving identity")
    if not digest.is_sha256(release_receipt_sha256):
        raise errors.InstallError("payload manifest has no valid release receipt identity")
    return {
        "transaction_id": uuid.uuid4().hex,
        "release": release,
        "serving_payload_sha256": serving_payload_sha256,
        "release_receipt_sha256": release_receipt_sha256,
        "manifest_sha256": manifest_sha256,
    }


def post_ready(
    ctx: runtime_context.RuntimeContext,
    expected: RuntimeSnapshot,
    *,
    lease_seconds: float | None = None,
    timeout_seconds: float = 5.0,
) -> RuntimeSnapshot:
    """POST the loopback handoff endpoint and require a protocol-v2 READY ack."""
    body = {
        "transaction_id": expected["transaction_id"],
        "release": expected["release"],
        "serving_payload_sha256": expected["serving_payload_sha256"],
        "release_receipt_sha256": expected["release_receipt_sha256"],
        "manifest_sha256": expected["manifest_sha256"],
    }
    if lease_seconds is not None:
        body["lease_seconds"] = max(1, int(lease_seconds))
    body["timeout_seconds"] = max(1, min(120, int(timeout_seconds)))
    data = json.dumps(body).encode("utf-8")
    if len(data) > _MAX_BODY_BYTES:
        raise errors.InstallError("handoff request payload is too large")
    request = urllib.request.Request(
        runtime_config.loopback_url(ctx.port, "/control/handoff"),
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    loaded_response = _handoff_reply(
        request, timeout_seconds=timeout_seconds + _TRANSPORT_MARGIN_SECONDS
    )
    if not isinstance(loaded_response, dict) or not all(
        isinstance(key, str) for key in loaded_response
    ):
        raise errors.InstallError("handoff control returned an invalid response")
    response_payload: RuntimeSnapshot = {
        key: value for key, value in loaded_response.items() if isinstance(key, str)
    }
    if not fields_match(
        response_payload,
        {"ok": True, "state": "ready", "protocol_version": HANDOFF_PROTOCOL_VERSION},
    ):
        raise errors.InstallError(
            "handoff control did not return a protocol-v2 READY acknowledgement"
        )
    if response_payload.get("transaction_id") != expected["transaction_id"]:
        raise errors.InstallError("handoff control acknowledged an unexpected transaction")
    child_pid = response_payload.get("child_pid")
    if not positive_int(child_pid):
        raise errors.InstallError("handoff control response is missing a valid child pid")
    return response_payload


def predecessor_serves(
    ctx: runtime_context.RuntimeContext,
    *,
    failure: BaseException,
    source_listener: process.OwnedProcess,
    source_runtime: RuntimeSnapshot,
    runtime_reader: RuntimeReader,
) -> bool:
    """Return whether the captured predecessor owns the listener with no handoff begun since.

    ``failure`` is what ended the handoff request. A handoff that is in flight may yet
    commit its successor, so it proves nothing.
    """
    runtime = runtime_reader(ctx)
    return _runtime_is_captured_source(
        source_listener, runtime, source_runtime
    ) and _old_runtime_resumed(
        runtime, source_runtime, refused=isinstance(failure, HandoffRefusedError)
    )


def request(
    ctx: runtime_context.RuntimeContext,
    expected: RuntimeSnapshot,
    *,
    source_listener: process.OwnedProcess,
    source_runtime: RuntimeSnapshot,
    runtime_reader: RuntimeReader,
    timeout_seconds: float = 30.0,
    lease_seconds: float = 30.0,
) -> RuntimeSnapshot:
    """Ask one verified listener to hand off and prove the finalized successor.

    ``expected`` must bind the transaction identifier, release, aggregate
    serving-payload digest, release-receipt digest, and manifest digest.
    """
    if not _runtime_is_captured_source(source_listener, runtime_reader(ctx), source_runtime):
        raise errors.InstallError(
            f"captured proxy listener generation {source_listener.pid} is no longer active"
        )
    old_pid = source_listener.pid
    ready = post_ready(
        ctx,
        expected,
        lease_seconds=lease_seconds,
        timeout_seconds=timeout_seconds,
    )
    child_pid = ready["child_pid"]
    if not positive_int(child_pid):
        raise errors.InstallError("handoff control response lost its verified child pid")
    successor = process.wait_for_executable(
        child_pid,
        ctx.executable,
        roles={serving_roles.HANDOFF_CHILD_MODE},
        timeout_seconds=timeout_seconds,
    )
    if successor is None:
        raise errors.InstallError("handoff control response names an unverified successor process")
    convergence_seconds = timeout_seconds * 3 + max(1.0, lease_seconds) + 5.0
    deadline = time.monotonic() + convergence_seconds
    while time.monotonic() < deadline:
        runtime = runtime_reader(ctx)
        if _successor_is_finalized(
            source_listener,
            successor,
            runtime=runtime,
            expected=expected,
            child_pid=child_pid,
        ):
            return {
                "old_pid": old_pid,
                "new_pid": child_pid,
                "child_pid": child_pid,
                "transaction_id": expected["transaction_id"],
                "release": expected["release"],
                "runtime": runtime,
            }
        time.sleep(0.1)
    raise errors.InstallError(
        f"handoff did not converge on finalized listener {child_pid} "
        f"within {convergence_seconds:g}s"
    )


def resolve_after_controller_failure(
    ctx: runtime_context.RuntimeContext,
    old_runtime: RuntimeSnapshot,
    expected: RuntimeSnapshot,
    *,
    failure: BaseException,
    source_listener: process.OwnedProcess,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
    lease_seconds: float,
) -> tuple[str, RuntimeSnapshot | None]:
    """Resolve caller failure without racing listener-owned finalization.

    ``failure`` is what ended the handoff request.
    """
    old_pid = old_runtime["pid"]
    if not positive_int(old_pid):
        return "unknown", None
    refused = isinstance(failure, HandoffRefusedError)
    deadline = time.monotonic() + timeout_seconds + max(1.0, lease_seconds) + 5.0
    while time.monotonic() < deadline:
        runtime = runtime_reader(ctx)
        if isinstance(runtime, dict):
            pid = runtime.get("pid")
            if (
                positive_int(pid)
                and pid != old_pid
                and (
                    successor := process.capture_executable(
                        pid,
                        ctx.executable,
                        roles={serving_roles.HANDOFF_CHILD_MODE},
                    )
                )
                is not None
                and _successor_is_finalized(
                    source_listener,
                    successor,
                    runtime=runtime,
                    expected=expected,
                    child_pid=pid,
                )
            ):
                return "finalized", runtime
            if _runtime_is_captured_source(
                source_listener, runtime, old_runtime
            ) and _old_runtime_resumed(runtime, old_runtime, refused=refused):
                return "rolled_back", runtime
        time.sleep(0.1)
    return "unknown", None


def _successor_is_finalized(
    predecessor: process.OwnedProcess,
    successor: process.OwnedProcess,
    *,
    runtime: RuntimeSnapshot | None,
    expected: RuntimeSnapshot,
    child_pid: int,
) -> bool:
    """Prove predecessor exit and one live finalized successor generation."""
    return (
        not process.owned_process_alive(predecessor)
        and process.owned_process_alive(successor)
        and _runtime_matches(runtime, expected, child_pid)
    )


def _runtime_is_captured_source(
    source_listener: process.OwnedProcess,
    runtime: RuntimeSnapshot | None,
    source_runtime: RuntimeSnapshot,
) -> bool:
    """Bind loopback admission to one captured predecessor generation and payload."""
    return (
        process.owned_process_alive(source_listener)
        and isinstance(runtime, dict)
        and source_runtime.get("pid") == source_listener.pid
        and isinstance(source_runtime.get("release"), str)
        and bool(source_runtime["release"])
        and all(digest.is_sha256(source_runtime.get(field)) for field in RUNTIME_DIGEST_FIELDS)
        and runtime.get("pid") == source_listener.pid
        and all(
            runtime.get(field) == source_runtime.get(field) for field in _RUNTIME_IDENTITY_FIELDS
        )
    )


def _runtime_matches(
    runtime: RuntimeSnapshot | None,
    expected: RuntimeSnapshot,
    child_pid: int,
) -> bool:
    """Require the transaction-complete successor identity."""
    if not isinstance(runtime, dict):
        return False
    return identity.runtime_payload_matches(runtime, expected) and fields_match(
        runtime,
        {
            "pid": child_pid,
            "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
            "handoff_transaction_id": expected["transaction_id"],
            "handoff_state": "finalized",
            "accepting": True,
            "draining": False,
        },
    )


def _old_runtime_resumed(
    runtime: RuntimeSnapshot | None,
    old_runtime: RuntimeSnapshot,
    *,
    refused: bool,
) -> bool:
    """Return whether the captured listener serves with no handoff begun since its capture.

    Its handoff session is idle, or it is still the finalized one that the capture showed and
    the listener refused the request. A listener that a handoff created reports that session
    until its next handoff begins, and beginning one replaces it for good; so the session
    alone says only that the request has not begun yet. The refusal says that its handler has
    finished. Without it the listener may have read the request and be about to begin.
    """
    if not isinstance(runtime, dict):
        return False
    session = (runtime.get("handoff_state"), runtime.get("handoff_transaction_id"))
    captured = (old_runtime.get("handoff_state"), old_runtime.get("handoff_transaction_id"))
    at_rest = session == ("idle", None) or (
        refused
        and captured[0] == "finalized"
        and isinstance(captured[1], str)
        and bool(captured[1])
        and session == captured
    )
    return at_rest and fields_match(
        runtime,
        {
            "pid": old_runtime["pid"],
            "serving_payload_sha256": old_runtime.get("serving_payload_sha256"),
            "release_receipt_sha256": old_runtime.get("release_receipt_sha256"),
            "payload_manifest_sha256": old_runtime.get("payload_manifest_sha256"),
            "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
            "accepting": True,
            "draining": False,
        },
    )
