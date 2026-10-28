"""Performance evidence names the native payload that it measured."""

from __future__ import annotations

import hashlib
from pathlib import Path

from openai_responses_proxy.serving import identity
from tools.performance import payload

_HANDOFF = {
    "release": "9.9.9",
    "serving_payload_sha256": "a" * 64,
    "release_receipt_sha256": "b" * 64,
    "manifest_sha256": "c" * 64,
}


def test_metadata_names_the_committed_payload_and_its_executable(tmp_path: Path) -> None:
    executable = tmp_path / "openai-responses-proxy"
    executable.write_bytes(b"native fixture")
    assert payload.metadata(_HANDOFF, executable) == {
        "payload_release": "9.9.9",
        "payload_serving_payload_sha256": "a" * 64,
        "payload_release_receipt_sha256": "b" * 64,
        "payload_manifest_sha256": "c" * 64,
        "payload_executable_sha256": hashlib.sha256(b"native fixture").hexdigest(),
    }


def test_fields_follow_the_committed_payload_identity(tmp_path: Path) -> None:
    # Verification requires exactly these fields, so they must stay those that a
    # committed payload hands off plus its executable's digest.
    executable = tmp_path / "openai-responses-proxy"
    executable.write_bytes(b"native fixture")
    committed = identity.LoadedPayloadIdentity(
        _HANDOFF["release"],
        _HANDOFF["serving_payload_sha256"],
        _HANDOFF["release_receipt_sha256"],
        _HANDOFF["manifest_sha256"],
        tmp_path,
    )
    assert set(payload.metadata(committed.handoff(), executable)) == set(payload.FIELDS)
