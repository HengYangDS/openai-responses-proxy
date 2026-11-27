"""Single owner for native installed-runtime identity."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING
from typing import Final

from openai_responses_proxy import product_identity
from openai_responses_proxy.serving import digest

if TYPE_CHECKING:
    from collections.abc import Mapping

MANIFEST_FILENAME = "payload-manifest.json"
MANIFEST_SCHEMA_VERSION: Final = 2
RELEASE_RECEIPT_FILENAME = "release-asset-receipt.json"
INSTALLED_RELEASE_STATE_FILENAME = "release-install-state.json"
RUNTIME_CONFIG_FILENAME = "runtime-config.json"
EXECUTABLE = f"bin/{product_identity.COMMAND_NAME}"
# The members that every native bundle holds.
REQUIRED_RUNTIME_FILES = frozenset((EXECUTABLE,))


def is_runtime_file(relative: str) -> bool:
    """Return whether a canonical member belongs to a native bundle."""
    return relative.startswith("bin/")


def installed_executable(root: str) -> str:
    """Return the executable below an installed data root."""
    return posixpath.join(root, EXECUTABLE)


def validated_serving_payload_sha256(file_digests: Mapping[str, str]) -> str:
    """Return the aggregate only for the exact serving file set."""
    paths = set(file_digests)
    if not REQUIRED_RUNTIME_FILES.issubset(paths) or any(
        not is_runtime_file(relative) for relative in paths
    ):
        raise digest.PayloadDigestError("serving payload files do not match the declared inventory")
    return digest.aggregate_file_digests_sha256(file_digests)
