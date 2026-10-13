"""Freeze the exact loaded serving payload before listener startup."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory

_RUNTIME_PAYLOAD_FIELDS = {
    "release": "release",
    "serving_payload_sha256": "serving_payload_sha256",
    "release_receipt_sha256": "release_receipt_sha256",
    "payload_manifest_sha256": "manifest_sha256",
}
PAYLOAD_SELECTOR_FILENAME = "payload-selection.json"
PAYLOAD_SELECTOR_SCHEMA = 1
PAYLOAD_GENERATIONS_DIRNAME = "generations"
_PAYLOAD_SELECTOR_FIELDS = frozenset({"schema_version", "active", "predecessor"})


@dataclass(frozen=True, slots=True)
class LoadedPayloadIdentity:
    """Release and source identities captured before listener startup."""

    release: str
    serving_payload_sha256: str
    release_receipt_sha256: str
    manifest_sha256: str
    root: Path

    def handoff(self) -> dict[str, str]:
        """Return the fields exchanged by protocol-v2 handoff."""
        return {
            "release": self.release,
            "serving_payload_sha256": self.serving_payload_sha256,
            "release_receipt_sha256": self.release_receipt_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class PayloadSelection:
    """The sole active payload generation and optional retained predecessor."""

    active: str
    predecessor: str | None


def runtime_payload_matches(runtime: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    """Match one runtime snapshot to the canonical payload identity fields."""
    return all(
        runtime.get(runtime_field) == expected.get(expected_field)
        for runtime_field, expected_field in _RUNTIME_PAYLOAD_FIELDS.items()
    )


def freeze_loaded_payload(executable: Path) -> LoadedPayloadIdentity | None:
    """Validate and freeze the manifest-owned files loaded by this process."""
    try:
        root, windows = _runtime_root(executable)
        manifest = _read_manifest(root / inventory.MANIFEST_FILENAME)
        aggregate = _verify_runtime_files(
            root, _digest_mapping(manifest, "serving_files"), windows=windows
        )
        return _identity(root, manifest, aggregate, require_receipt_file=False)
    except (
        IndexError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        digest.PayloadDigestError,
    ):
        return None


def selected_payload_executable(executable: Path) -> Path:
    """Resolve the selector-bound generation executable from one loaded payload."""
    loaded = freeze_loaded_payload(executable)
    if loaded is None:
        raise ValueError("loaded payload identity is unavailable")
    control_root = (
        loaded.root.parent.parent
        if loaded.root.parent.name == PAYLOAD_GENERATIONS_DIRNAME
        else loaded.root
    )
    selected = read_payload_selection(control_root / PAYLOAD_SELECTOR_FILENAME)
    candidate = (
        control_root / PAYLOAD_GENERATIONS_DIRNAME / selected.active / "bin" / executable.name
    )
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("selected payload executable is unavailable")
    resolved = candidate.resolve(strict=True)
    selected_identity = committed_payload(resolved)
    if selected_identity is None or selected_identity.root != candidate.parents[1]:
        raise ValueError("selected payload identity is invalid")
    return resolved


def read_payload_selection(selector: Path) -> PayloadSelection:
    """Read the one canonical selector schema shared by runtime and lifecycle code."""
    if selector.is_symlink() or not selector.is_file():
        raise ValueError("payload generation selector is invalid")
    try:
        content = selector.read_bytes()
        value: object = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload generation selector is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != _PAYLOAD_SELECTOR_FIELDS
        or value.get("schema_version") != PAYLOAD_SELECTOR_SCHEMA
        or digest.canonical_json(value) != content
    ):
        raise ValueError("payload generation selector is invalid")
    active = require_payload_generation_name(value.get("active"))
    predecessor_value = value.get("predecessor")
    predecessor = (
        None if predecessor_value is None else require_payload_generation_name(predecessor_value)
    )
    if predecessor == active:
        raise ValueError("payload generation selector is invalid")
    return PayloadSelection(active, predecessor)


def require_payload_generation_name(value: object) -> str:
    """Return one canonical immutable-generation identifier."""
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("payload generation identity is invalid")
    return value


def committed_payload(executable: Path) -> LoadedPayloadIdentity | None:
    """Return the complete successor identity currently committed on disk."""
    try:
        root, windows = _runtime_root(executable)
        manifest = _read_manifest(root / inventory.MANIFEST_FILENAME)
        if manifest.get("schema_version") != 2:
            raise ValueError("installed manifest schema mismatch")
        files = _digest_mapping(manifest, "files")
        serving = _digest_mapping(manifest, "serving_files")
        if serving != files:
            raise ValueError("serving digest mismatch")
        aggregate = _verify_runtime_files(root, files, windows=windows)
        return _identity(root, manifest, aggregate, require_receipt_file=True)
    except (
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        digest.PayloadDigestError,
    ):
        return None


def _runtime_root(executable: Path) -> tuple[Path, bool]:
    resolved = executable.resolve(strict=True)
    root = resolved.parents[1]
    if resolved == root / inventory.EXECUTABLE:
        return root, False
    if resolved == root / inventory.WINDOWS_EXECUTABLE:
        return root, True
    raise ValueError("executable is outside the installed runtime identity")


def _digest_mapping(manifest: Mapping[str, object], field: str) -> dict[str, str]:
    value = manifest.get(field)
    if not isinstance(value, dict) or any(
        not isinstance(path, str) or not isinstance(expected, str)
        for path, expected in value.items()
    ):
        raise ValueError(f"installed manifest {field} mapping is invalid")
    return cast("dict[str, str]", value)


def _verify_runtime_files(root: Path, expected: Mapping[str, str], *, windows: bool) -> str:
    paths = set(expected)
    if not inventory.required_runtime_files(windows=windows).issubset(paths) or any(
        not inventory.is_runtime_file(path, windows=windows) for path in paths
    ):
        raise ValueError("installed inventory does not match the executable platform")
    aggregate = inventory.validated_serving_payload_sha256(expected)
    if any(digest.sha256_file(root / path) != value for path, value in expected.items()):
        raise ValueError("installed payload digest mismatch")
    return aggregate


def _identity(
    root: Path,
    manifest: Mapping[str, object],
    aggregate: str,
    *,
    require_receipt_file: bool,
) -> LoadedPayloadIdentity:
    receipt = manifest.get("release_receipt_sha256")
    release = manifest.get("release")
    if (
        not isinstance(release, str)
        or not release
        or manifest.get("serving_payload_sha256") != aggregate
        or not digest.is_sha256(receipt)
        or (
            require_receipt_file
            and digest.sha256_file(root / inventory.RELEASE_RECEIPT_FILENAME) != receipt
        )
    ):
        raise ValueError("installed successor identity mismatch")
    return LoadedPayloadIdentity(
        release,
        aggregate,
        receipt,
        digest.sha256_file(root / inventory.MANIFEST_FILENAME),
        root,
    )


def _read_manifest(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError("payload manifest must be an object")
    return {key: item for key, item in value.items() if isinstance(key, str)}
