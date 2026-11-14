"""Handoff admission of a live runtime from its identity and handoff capabilities."""

from __future__ import annotations

from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction


def _available_transaction(state: object, transaction_id: object) -> bool:
    if state == "idle":
        return transaction_id is None
    if state != "finalized":
        return False
    return isinstance(transaction_id, str) and bool(transaction_id)


def _identity_is_valid(runtime: handoff.RuntimeSnapshot | None) -> bool:
    """Return whether runtime identity permits a lifecycle transition."""
    if not isinstance(runtime, dict):
        return False
    digests_valid = all(
        digest.is_sha256(runtime.get(field)) for field in handoff.RUNTIME_DIGEST_FIELDS
    )
    pid = runtime.get("pid")
    release = runtime.get("release")
    if not all(
        (
            handoff.positive_int(pid),
            isinstance(release, str) and bool(release),
            digests_valid,
            handoff.fields_match(
                runtime,
                {
                    "handoff_protocol_version": handoff.HANDOFF_PROTOCOL_VERSION,
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


def runtime_supports_handoff(runtime: handoff.RuntimeSnapshot | None) -> bool:
    """Return whether a live runtime has a valid identity and both handoff capabilities."""
    if not _identity_is_valid(runtime) or not isinstance(runtime, dict):
        return False
    capabilities = runtime.get("handoff_capabilities")
    return (
        isinstance(capabilities, list)
        and all(isinstance(capability, str) for capability in capabilities)
        and handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY in capabilities
        and handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY in capabilities
    )
