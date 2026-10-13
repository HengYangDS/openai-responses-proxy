"""Controller-side transport and proof for protocol-v2 rolling handoff.

This module owns the loopback handoff request, successor identity checks, and
bounded convergence or rollback observations. Installed :mod:`control` invokes
it only for same-payload reload. Source-side
:mod:`openai_responses_proxy.lifecycle.deployment.apply` invokes it after a released
transaction commits a different admitted transaction.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal
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
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction

HANDOFF_PROTOCOL_VERSION = 2
_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_MARGIN_SECONDS = 1.0
_RUNTIME_DIGEST_FIELDS = [
    "serving_payload_sha256",
    "release_receipt_sha256",
    "payload_manifest_sha256",
]
_RUNTIME_IDENTITY_FIELDS = ["release", *_RUNTIME_DIGEST_FIELDS]

RuntimeSnapshot = dict[str, object]
RuntimeReader = Callable[[runtime_context.RuntimeContext], RuntimeSnapshot | None]
type DeploymentStrategy = Literal["handoff", "native_generation", "unsupported"]
_LISTENER_ROLES = {serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE}


def _fields_match(actual: RuntimeSnapshot, expected: dict[str, object]) -> bool:
    return all(actual.get(field) == value for field, value in expected.items())


def _positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _available_transaction(state: object, transaction_id: object) -> bool:
    if state == "idle":
        return transaction_id is None
    if state != "finalized":
        return False
    return isinstance(transaction_id, str) and bool(transaction_id)


def runtime_supports_selected_generation_handoff(
    runtime: RuntimeSnapshot | None,
) -> bool:
    """Return whether a runtime can launch the selected payload generation."""
    if not isinstance(runtime, dict):
        return False
    capabilities = runtime.get("handoff_capabilities")
    return (
        isinstance(capabilities, list)
        and all(isinstance(capability, str) for capability in capabilities)
        and handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY in capabilities
    )


def runtime_supports_admission_preserving_handoff(
    runtime: RuntimeSnapshot | None,
) -> bool:
    """Return whether selected-generation handoff keeps admission open."""
    if not runtime_supports_selected_generation_handoff(runtime):
        return False
    assert isinstance(runtime, dict)
    capabilities = runtime["handoff_capabilities"]
    assert isinstance(capabilities, list)
    return handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY in capabilities


def deployment_strategy_identity_is_valid(runtime: RuntimeSnapshot | None) -> bool:
    """Return whether runtime identity permits a lifecycle transition."""
    if not isinstance(runtime, dict):
        return False
    digests_valid = all(digest.is_sha256(runtime.get(field)) for field in _RUNTIME_DIGEST_FIELDS)
    pid = runtime.get("pid")
    release = runtime.get("release")
    if not all(
        (
            _positive_int(pid),
            isinstance(release, str) and bool(release),
            digests_valid,
            _fields_match(
                runtime,
                {
                    "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
                    "accepting": True,
                    "draining": False,
                },
            ),
        )
    ):
        return False
    return _available_transaction(
        runtime.get("handoff_state"),
        runtime.get("handoff_transaction_id"),
    )


def deployment_strategy(runtime: RuntimeSnapshot | None) -> DeploymentStrategy:
    """Select the safe deployment transition proved by runtime capabilities."""
    if not deployment_strategy_identity_is_valid(runtime):
        return "unsupported"
    if runtime_supports_admission_preserving_handoff(runtime):
        return "handoff"
    return "native_generation"


def runtime_supports_handoff(runtime: RuntimeSnapshot | None) -> bool:
    """Return whether a live runtime explicitly supports selected-generation handoff."""
    if not isinstance(runtime, dict):
        return False
    return deployment_strategy_identity_is_valid(
        runtime
    ) and runtime_supports_selected_generation_handoff(runtime)


def capture_source_listener(
    ctx: runtime_context.RuntimeContext,
    runtime: RuntimeSnapshot,
) -> process.OwnedProcess:
    """Boundedly prove one runtime process owns admission before mutation."""
    pid = runtime.get("pid")
    if not _positive_int(pid):
        raise errors.InstallError("installed runtime identity is not verified")
    owned = process.capture_executable(pid, ctx.executable, roles=_LISTENER_ROLES)
    if owned is None:
        raise errors.InstallError("installed listener process generation is not verified")
    if not process.owned_process_alive(owned):
        raise errors.InstallError("installed listener process generation is not verified")
    return owned


def expected_metadata(root: str) -> RuntimeSnapshot:
    """Read release, aggregate payload, receipt, and manifest identity."""
    try:
        manifest_path = os.path.join(root, inventory.MANIFEST_FILENAME)
        with open(manifest_path, encoding="utf-8") as handle:
            loaded: object = json.load(handle)
        if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
            raise TypeError("payload manifest must be an object with string keys")
        manifest = {key: value for key, value in loaded.items() if isinstance(key, str)}
        release = manifest["release"]
        serving_payload_sha256 = manifest["serving_payload_sha256"]
        release_receipt_sha256 = manifest["release_receipt_sha256"]
        manifest_sha256 = digest.sha256_file(Path(manifest_path))
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
    try:
        with loopback.open_request(
            request,
            timeout_seconds=timeout_seconds + _TRANSPORT_MARGIN_SECONDS,
        ) as response:
            if response.status != 202:
                raise errors.InstallError(f"handoff control returned HTTP {response.status}")
            raw = response.read(_MAX_BODY_BYTES + 1)
            if len(raw) > _MAX_BODY_BYTES:
                raise errors.InstallError("handoff control response is too large")
            loaded_response: object = json.loads(raw)
    except errors.InstallError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise errors.InstallError(f"handoff control returned HTTP {code}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise errors.InstallError("handoff control is unavailable") from exc
    if not isinstance(loaded_response, dict) or not all(
        isinstance(key, str) for key in loaded_response
    ):
        raise errors.InstallError("handoff control returned an invalid response")
    response_payload: RuntimeSnapshot = {}
    for key, value in loaded_response.items():
        assert isinstance(key, str)
        response_payload[key] = value
    if not _fields_match(
        response_payload,
        {"ok": True, "state": "ready", "protocol_version": HANDOFF_PROTOCOL_VERSION},
    ):
        raise errors.InstallError(
            "handoff control did not return a protocol-v2 READY acknowledgement"
        )
    if response_payload.get("transaction_id") != expected["transaction_id"]:
        raise errors.InstallError("handoff control acknowledged an unexpected transaction")
    child_pid = response_payload.get("child_pid")
    if not _positive_int(child_pid):
        raise errors.InstallError("handoff control response is missing a valid child pid")
    return response_payload


def drain_responses(
    ctx: runtime_context.RuntimeContext,
    *,
    source_listener: process.OwnedProcess,
    source_runtime: RuntimeSnapshot,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> None:
    """Close predecessor admission and wait for its accepted Responses to finish."""
    if not _runtime_is_captured_source(source_listener, runtime_reader(ctx), source_runtime):
        raise errors.InstallError(
            f"captured proxy listener generation {source_listener.pid} is no longer active"
        )
    request = urllib.request.Request(
        runtime_config.loopback_url(ctx.port, "/control/drain"),
        headers={
            "Accept": "application/json",
            "X-OpenAI-Responses-Proxy-Drain-Lease-Seconds": str(
                max(1, math.ceil(timeout_seconds + _TRANSPORT_MARGIN_SECONDS))
            ),
        },
        method="POST",
    )
    try:
        with loopback.open_request(
            request,
            timeout_seconds=min(timeout_seconds, 5.0) + _TRANSPORT_MARGIN_SECONDS,
        ) as response:
            if response.status != 200:
                raise errors.InstallError(f"drain control returned HTTP {response.status}")
            raw = response.read(_MAX_BODY_BYTES + 1)
            if len(raw) > _MAX_BODY_BYTES:
                raise errors.InstallError("drain control response is too large")
            acknowledged: object = json.loads(raw)
    except errors.InstallError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise errors.InstallError(f"drain control returned HTTP {code}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise errors.InstallError("drain control is unavailable") from exc
    if not isinstance(acknowledged, dict) or acknowledged.get("draining") is not True:
        raise errors.InstallError("drain control did not close Responses admission")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        runtime = runtime_reader(ctx)
        if not _runtime_is_captured_source(source_listener, runtime, source_runtime):
            raise errors.InstallError("predecessor changed while draining active Responses")
        assert runtime is not None
        if (
            runtime.get("draining") is True
            and runtime.get("accepting") is False
            and runtime.get("active_responses") == 0
            and runtime.get("active_handlers") == 1
        ):
            return
        time.sleep(0.1)
    raise errors.InstallError(
        f"predecessor did not drain active Responses within {timeout_seconds:g}s"
    )


def resume_responses(
    ctx: runtime_context.RuntimeContext,
    *,
    source_listener: process.OwnedProcess,
    source_runtime: RuntimeSnapshot,
    runtime_reader: RuntimeReader,
) -> bool:
    """Reopen admission on the exact predecessor that survived a failed replacement."""
    runtime = runtime_reader(ctx)
    if not _runtime_is_captured_source(source_listener, runtime, source_runtime):
        return False
    request = urllib.request.Request(
        runtime_config.loopback_url(ctx.port, "/control/drain"),
        headers={"Accept": "application/json"},
        method="DELETE",
    )
    try:
        with loopback.open_request(
            request,
            timeout_seconds=_TRANSPORT_MARGIN_SECONDS,
        ) as response:
            if response.status != 200:
                raise errors.InstallError(f"drain release returned HTTP {response.status}")
            raw = response.read(_MAX_BODY_BYTES + 1)
            if len(raw) > _MAX_BODY_BYTES:
                raise errors.InstallError("drain release response is too large")
            resumed: object = json.loads(raw)
    except errors.InstallError:
        raise
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise errors.InstallError(f"drain release returned HTTP {code}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise errors.InstallError("drain release is unavailable") from exc
    if not isinstance(resumed, dict) or resumed.get("draining") is not False:
        raise errors.InstallError("drain release did not reopen Responses admission")
    runtime = runtime_reader(ctx)
    if not _runtime_is_captured_source(source_listener, runtime, source_runtime):
        return False
    assert runtime is not None
    return runtime.get("accepting") is True and runtime.get("draining") is False


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
    if not _positive_int(child_pid):
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


def wait_for_rollback(
    ctx: runtime_context.RuntimeContext,
    old_runtime: RuntimeSnapshot,
    *,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> RuntimeSnapshot:
    """Confirm the exact old process resumed normal admission after ABORT."""
    old_pid = old_runtime["pid"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.verified_proxy_listener_pids(ctx) == [old_pid]:
            runtime = runtime_reader(ctx)
            if _old_runtime_resumed(runtime, old_runtime):
                assert runtime is not None
                return runtime
        time.sleep(0.1)
    raise errors.InstallError(
        f"old proxy listener {old_pid} did not resume after handoff rollback within {timeout_seconds:g}s"
    )


def resolve_after_controller_failure(
    ctx: runtime_context.RuntimeContext,
    old_runtime: RuntimeSnapshot,
    expected: RuntimeSnapshot,
    *,
    source_listener: process.OwnedProcess,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
    lease_seconds: float,
) -> tuple[str, RuntimeSnapshot | None]:
    """Resolve caller failure without racing listener-owned finalization."""
    old_pid = old_runtime["pid"]
    if not _positive_int(old_pid):
        return "unknown", None
    deadline = time.monotonic() + timeout_seconds + max(1.0, lease_seconds) + 5.0
    while time.monotonic() < deadline:
        runtime = runtime_reader(ctx)
        if isinstance(runtime, dict):
            pid = runtime.get("pid")
            if (
                _positive_int(pid)
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
            ) and _old_runtime_resumed(runtime, old_runtime):
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
        and all(digest.is_sha256(source_runtime.get(field)) for field in _RUNTIME_DIGEST_FIELDS)
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
    return identity.runtime_payload_matches(runtime, expected) and _fields_match(
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
) -> bool:
    if not isinstance(runtime, dict):
        return False
    return _fields_match(
        runtime,
        {
            "pid": old_runtime["pid"],
            "serving_payload_sha256": old_runtime.get("serving_payload_sha256"),
            "release_receipt_sha256": old_runtime.get("release_receipt_sha256"),
            "payload_manifest_sha256": old_runtime.get("payload_manifest_sha256"),
            "handoff_protocol_version": HANDOFF_PROTOCOL_VERSION,
            "handoff_state": "idle",
            "handoff_transaction_id": None,
            "accepting": True,
            "draining": False,
        },
    )
