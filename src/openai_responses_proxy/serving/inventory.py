"""Single owner for native installed-runtime identity."""

from __future__ import annotations

from collections.abc import Mapping

from openai_responses_proxy import product_identity
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import digest

MANIFEST_FILENAME = "payload-manifest.json"
RELEASE_RECEIPT_FILENAME = "release-asset-receipt.json"
INSTALLED_RELEASE_STATE_FILENAME = "release-install-state.json"
RUNTIME_CONFIG_FILENAME = "runtime-config.json"
PROVIDER_MANIFEST = "providers.toml"
EXECUTABLE = f"bin/{product_identity.executable_name(windows=False)}"
WINDOWS_EXECUTABLE = f"bin/{product_identity.executable_name(windows=True)}"


def executable_name(*, windows: bool = False) -> str:
    """Return the platform archive member owned by the runtime."""
    return WINDOWS_EXECUTABLE if windows else EXECUTABLE


def required_runtime_files(*, windows: bool = False) -> frozenset[str]:
    """Return members required in every native bundle for one platform."""
    return frozenset((executable_name(windows=windows), PROVIDER_MANIFEST))


def is_runtime_file(relative: str, *, windows: bool = False) -> bool:
    """Return whether a canonical member belongs to the current native bundle."""
    executable = executable_name(windows=windows)
    return relative in (PROVIDER_MANIFEST, executable) or relative.startswith("bin/")


def installed_executable(root: str, *, windows: bool = False) -> str:
    """Return the platform executable below an installed data root."""
    return config.path_join(root, *executable_name(windows=windows).split("/"))


def validated_serving_payload_sha256(file_digests: Mapping[str, str]) -> str:
    """Return the aggregate only for the exact serving file set."""
    paths = set(file_digests)
    windows = WINDOWS_EXECUTABLE in paths
    if not required_runtime_files(windows=windows).issubset(paths) or any(
        not is_runtime_file(relative, windows=windows) for relative in paths
    ):
        raise digest.PayloadDigestError("serving payload files do not match the declared inventory")
    return digest.aggregate_file_digests_sha256(file_digests)
