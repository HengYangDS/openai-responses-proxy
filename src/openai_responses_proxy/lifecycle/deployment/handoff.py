"""Controller-side transport and proof for protocol-v2 rolling handoff.

This module owns the loopback handoff request, successor identity checks, and
bounded convergence or rollback observations. Installed :mod:`control` invokes
it only for same-payload reload. Source-side
:mod:`openai_responses_proxy.lifecycle.deployment.apply` invokes it after a released
transaction commits a different admitted transaction.
"""

from __future__ import annotations

import enum
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
_MAX_REQUEST_TIMEOUT_SECONDS = 120
# Far above the resolution of a platform's monotonic clock: a health read that is asked this
# long after a listener's bound is asked after the last begin that the bound admits.
_EXPIRY_MARGIN_SECONDS = 1.0
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


class RequestEvidence(enum.Enum):
    """What a controller holds that no handoff of its request is still to come.

    The listener never received the request, it has finished with it, or the time in which it
    may begin it has run out. The failure of the request carries the evidence to the resolver
    and to the proof.
    """

    NONE = "none"
    UNSENT = "unsent"
    REFUSED = "refused"
    ACKNOWLEDGED = "acknowledged"
    OBSERVED = "observed"
    EXPIRED = "expired"


_EVIDENCE_ATTRIBUTE = "handoff_request_evidence"


def _record_evidence(failure: BaseException, evidence: RequestEvidence) -> None:
    """Record on the failure of a handoff request what its controller holds of the listener."""
    vars(failure)[_EVIDENCE_ATTRIBUTE] = evidence


def request_evidence(failure: BaseException) -> RequestEvidence:
    """Return what the failure of a handoff request carries of the listener's part in it.

    A refusal is the failure's type. The request records on the failure that nothing was
    sent, or the listener's acknowledgement, and the resolver records an observation or that
    the request's time has run out. A failure that carries none of them holds no evidence,
    whatever its message says.
    """
    if isinstance(failure, HandoffRefusedError):
        return RequestEvidence.REFUSED
    recorded = vars(failure).get(_EVIDENCE_ATTRIBUTE)
    return recorded if isinstance(recorded, RequestEvidence) else RequestEvidence.NONE


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


_CONTROL = "handoff control"
_TRANSPORT_FAILURES = (OSError, urllib.error.URLError, ValueError, http.client.HTTPException)


def _connect(
    request: urllib.request.Request, *, timeout_seconds: float
) -> http.client.HTTPConnection:
    """Open the connection of a handoff request, or raise one public error with nothing sent.

    Every close of the connection is a reset, so a request that the controller gives up
    reaches a listener only on a connection that the listener already has in its queue.
    """
    try:
        return loopback.connect_reset_on_close(request, timeout_seconds=timeout_seconds)
    except _TRANSPORT_FAILURES as exc:
        raise errors.InstallError(f"{_CONTROL} is unavailable") from exc


def _handoff_reply(
    connection: http.client.HTTPConnection, request: urllib.request.Request
) -> object:
    """Read the bounded JSON reply of an accepted handoff request, or raise one public error."""
    label = _CONTROL
    try:
        with loopback.open_request_on(connection, request) as response:
            status = response.status
            raw = response.read(_MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise HandoffRefusedError(f"{label} returned HTTP {code}") from exc
    except _TRANSPORT_FAILURES as exc:
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


def carried_timeout_seconds(timeout_seconds: float) -> int:
    """Return the timeout that a handoff request carries to its listener.

    Whole seconds from 1 to 120, the longest that the protocol admits. A listener begins the
    request only within this time of accepting its connection, so the request and the wait
    that settles it when it stays unanswered take the value from here.
    """
    return max(1, min(_MAX_REQUEST_TIMEOUT_SECONDS, int(timeout_seconds)))


class _WaitAtRest:
    """The wait after which a listener at rest can no longer begin an unanswered request.

    A listener begins a request only within the carried timeout of accepting its connection,
    and it accepted that connection before the connection of any later health read. So the
    request can begin no later than that timeout after the first read that the listener
    answered, and what is read afterwards changes nothing of it: the wait starts once and is
    never started again. A read that is asked after the wait then shows every handoff that
    the request could begin, because a begin shows its session before it releases the lock
    that the read takes.
    """

    def __init__(self, timeout_seconds: float) -> None:
        """Bind the wait to the timeout that the caller gave the request."""
        self._seconds = carried_timeout_seconds(timeout_seconds) + _EXPIRY_MARGIN_SECONDS
        self._since: float | None = None

    def is_over(self, asked: float) -> bool:
        """Count a read that showed the captured listener at rest; return whether the wait is over.

        ``asked`` is the moment before the read was asked. The wait starts when the first
        such read has returned, because the listener answered it no later, and it is over
        only for a read that was asked after it, because the listener answered that no earlier.
        """
        if self._since is None:
            self._since = time.monotonic()
            return False
        return asked - self._since >= self._seconds


# The wait that settles a request whose own timeout is not known: the longest that a request
# can carry, and the margin.
LONGEST_WAIT_AT_REST_SECONDS = _MAX_REQUEST_TIMEOUT_SECONDS + _EXPIRY_MARGIN_SECONDS


def wait_out_any_request(answered: float) -> None:
    """Sleep until a listener can begin no request that it accepted before a health read.

    ``answered`` is the monotonic moment at which that read returned; the listener answered
    it no later. The caller does not know the request, so the wait is the longest one. A
    read that is asked after it shows every handoff that such a request could begin.

    Each sleep asks for all that remains, and the monotonic clock, which bounds the
    listener's begin, decides when the wait is over, however the platform's sleep counts.
    An interrupt ends the wait where it is.
    """
    until = answered + LONGEST_WAIT_AT_REST_SECONDS
    while (remaining := until - time.monotonic()) > 0:
        time.sleep(remaining)


def _ready_request(
    ctx: runtime_context.RuntimeContext,
    expected: RuntimeSnapshot,
    *,
    lease_seconds: float | None,
    timeout_seconds: float,
) -> urllib.request.Request:
    """Build the loopback handoff request without opening a connection."""
    body = {
        "transaction_id": expected["transaction_id"],
        "release": expected["release"],
        "serving_payload_sha256": expected["serving_payload_sha256"],
        "release_receipt_sha256": expected["release_receipt_sha256"],
        "manifest_sha256": expected["manifest_sha256"],
    }
    if lease_seconds is not None:
        body["lease_seconds"] = max(1, int(lease_seconds))
    body["timeout_seconds"] = carried_timeout_seconds(timeout_seconds)
    data = json.dumps(body).encode("utf-8")
    if len(data) > _MAX_BODY_BYTES:
        raise errors.InstallError("handoff request payload is too large")
    return urllib.request.Request(
        runtime_config.loopback_url(ctx.port, "/control/handoff"),
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )


def post_ready(
    ctx: runtime_context.RuntimeContext,
    expected: RuntimeSnapshot,
    *,
    lease_seconds: float | None = None,
    timeout_seconds: float = 5.0,
) -> RuntimeSnapshot:
    """POST the loopback handoff endpoint and require a protocol-v2 READY ack.

    A failure that is raised before the first byte of the request is written carries that
    nothing was sent: the building of the request, and the opening of its connection.
    """
    try:
        request = _ready_request(
            ctx, expected, lease_seconds=lease_seconds, timeout_seconds=timeout_seconds
        )
        connection = _connect(request, timeout_seconds=timeout_seconds + _TRANSPORT_MARGIN_SECONDS)
    except BaseException as failure:
        # Nothing was written: the listener cannot begin a request that it never received.
        _record_evidence(failure, RequestEvidence.UNSENT)
        raise
    # The request is written in this call. From here on a failure carries no such evidence.
    loaded_response = _handoff_reply(connection, request)
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
    """Return whether the captured predecessor owns the listener with no handoff to come.

    ``failure`` is what ended the handoff request, with the evidence that it carries. A
    handoff that is in flight may yet commit its successor, and one that the listener has
    not answered may yet begin, so neither proves anything.
    """
    runtime = runtime_reader(ctx)
    return _runtime_is_captured_source(
        source_listener, runtime, source_runtime
    ) and _old_runtime_resumed(runtime, source_runtime, evidence=request_evidence(failure))


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

    A failure carries what the controller holds of the request: that nothing was sent, on a
    failure before the request is written, the opening of its connection included; a refusal
    by its type; a READY acknowledgement on every failure that follows it. An interrupt
    carries the same as any other failure there. A failure of the sending or of the reading
    carries no evidence: the listener may have the request.
    """
    try:
        _require_captured_source(source_listener, runtime_reader(ctx), source_runtime)
    except BaseException as failure:
        # The check comes before the request: the listener can begin nothing of it.
        _record_evidence(failure, RequestEvidence.UNSENT)
        raise
    ready = post_ready(
        ctx,
        expected,
        lease_seconds=lease_seconds,
        timeout_seconds=timeout_seconds,
    )
    try:
        return _await_finalized_successor(
            ctx,
            expected,
            ready,
            source_listener=source_listener,
            runtime_reader=runtime_reader,
            timeout_seconds=timeout_seconds,
            lease_seconds=lease_seconds,
        )
    except BaseException as failure:
        # The listener answered READY: it began the handoff and its handler has finished.
        _record_evidence(failure, RequestEvidence.ACKNOWLEDGED)
        raise


def _await_finalized_successor(
    ctx: runtime_context.RuntimeContext,
    expected: RuntimeSnapshot,
    ready: RuntimeSnapshot,
    *,
    source_listener: process.OwnedProcess,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
    lease_seconds: float,
) -> RuntimeSnapshot:
    """Verify the successor that a READY acknowledgement names and wait for it to finalize."""
    old_pid = source_listener.pid
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

    ``failure`` is what ended the handoff request, with the evidence that it carries. With
    none the resolver waits: a listener that begins the request shows its transaction, which
    the resolver records on the failure, and one that is at rest in a read that is asked
    when the request's timeout has passed since it first showed itself at rest can no longer
    begin it, which the resolver records as well. A listener that is not at rest then, or
    not the captured one, leaves the outcome unknown at the deadline.
    """
    old_pid = old_runtime["pid"]
    if not positive_int(old_pid):
        return "unknown", None
    evidence = request_evidence(failure)
    wait = _WaitAtRest(timeout_seconds)
    deadline = time.monotonic() + timeout_seconds + max(1.0, lease_seconds) + 5.0
    while (asked := time.monotonic()) < deadline:
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
            if evidence is RequestEvidence.NONE and _names_request(runtime, expected):
                evidence = RequestEvidence.OBSERVED
                _record_evidence(failure, evidence)
            if _runtime_is_captured_source(source_listener, runtime, old_runtime):
                if (
                    evidence is RequestEvidence.NONE
                    # At rest as an expired request reads it; only the wait is still open.
                    and _old_runtime_resumed(runtime, old_runtime, evidence=RequestEvidence.EXPIRED)
                    and wait.is_over(asked)
                ):
                    evidence = RequestEvidence.EXPIRED
                    _record_evidence(failure, evidence)
                if _old_runtime_resumed(runtime, old_runtime, evidence=evidence):
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


def _require_captured_source(
    source_listener: process.OwnedProcess,
    runtime: RuntimeSnapshot | None,
    source_runtime: RuntimeSnapshot,
) -> None:
    """Raise unless loopback admission still belongs to the captured predecessor."""
    if not _runtime_is_captured_source(source_listener, runtime, source_runtime):
        raise errors.InstallError(
            f"captured proxy listener generation {source_listener.pid} is no longer active"
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


def _names_request(runtime: RuntimeSnapshot, expected: RuntimeSnapshot) -> bool:
    """Return whether a listener's session holds the transaction of this handoff request.

    A listener learns a transaction only from a request that names it, and holds it from the
    step that begins the handoff. So a request of this transaction was read and has begun:
    this one, while no earlier request of the same transaction is still outstanding.
    """
    transaction_id = expected.get("transaction_id")
    return (
        isinstance(transaction_id, str)
        and bool(transaction_id)
        and runtime.get("handoff_transaction_id") == transaction_id
    )


def _old_runtime_resumed(
    runtime: RuntimeSnapshot | None,
    old_runtime: RuntimeSnapshot,
    *,
    evidence: RequestEvidence,
) -> bool:
    """Return whether the captured listener serves with no handoff of this request to come.

    A session at rest says that no handoff is in flight. It says that none is still to come
    only with evidence that the listener never received the request or has finished with it,
    because a listener that has read the request and not begun it shows the same session:

    - unsent: an idle session, or the finalized one that the capture showed, unchanged.
      Nothing of this request can come.
    - refused: an idle session, or the finalized one that the capture showed, unchanged.
      Nothing began, or it began and ended.
    - acknowledged: an idle session. The handoff began and rolled back.
    - observed: an idle session. The handoff began and rolled back.
    - expired: an idle session, or the finalized one that the capture showed, unchanged.
      The listener can no longer begin the request, and nothing of it is in flight.
    - none: no session. The listener may be about to begin.

    A listener that a handoff created reports that finalized session until its next handoff
    begins, and beginning one replaces it for good; a failed handoff leaves it idle.
    """
    if not isinstance(runtime, dict):
        return False
    session = (runtime.get("handoff_state"), runtime.get("handoff_transaction_id"))
    captured = (old_runtime.get("handoff_state"), old_runtime.get("handoff_transaction_id"))
    idle = session == ("idle", None)
    unchanged = (
        captured[0] == "finalized"
        and isinstance(captured[1], str)
        and bool(captured[1])
        and session == captured
    )
    at_rest = {
        RequestEvidence.UNSENT: idle or unchanged,
        RequestEvidence.REFUSED: idle or unchanged,
        RequestEvidence.ACKNOWLEDGED: idle,
        RequestEvidence.OBSERVED: idle,
        RequestEvidence.EXPIRED: idle or unchanged,
        RequestEvidence.NONE: False,
    }[evidence]
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
