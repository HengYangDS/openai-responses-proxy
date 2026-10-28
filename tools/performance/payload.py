"""Name the native payload that a packaged performance measurement launches."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from openai_responses_proxy.serving import digest

# The pyperf run metadata that names a measured payload: the identity a
# committed payload hands off, and the digest of the executable launched.
# Performance verification requires exactly these fields.
FIELDS = (
    "payload_release",
    "payload_serving_payload_sha256",
    "payload_release_receipt_sha256",
    "payload_manifest_sha256",
    "payload_executable_sha256",
)


def metadata(handoff: Mapping[str, str], executable: Path) -> dict[str, str]:
    """Return the run metadata naming a committed payload and its executable."""
    named = {f"payload_{name}": value for name, value in handoff.items()}
    named["payload_executable_sha256"] = digest.sha256_file(executable)
    return named
