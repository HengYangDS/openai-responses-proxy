"""Validation and materialization of one admitted payload candidate."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from pathlib import PurePosixPath

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.json_value import thaw_value
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.runtime.process_environment import native_process_environment
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles

_STRICT_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def validate(
    blobs: tuple[artifact.ArtifactFile, ...],
    version: str,
    receipt_sha256: str,
    receipt: ReadOnlyJsonObject,
) -> None:
    """Reject an admitted candidate whose payload contract is inconsistent."""
    if _STRICT_VERSION.fullmatch(version) is None:
        raise errors.InstallError("released payload version is invalid")
    if not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64:
        raise errors.InstallError("released payload receipt digest is invalid")
    if receipt.get("version") != version:
        raise errors.InstallError("released payload receipt version mismatch")
    actual_paths = tuple(blob.path for blob in blobs)
    actual_set = set(actual_paths)
    windows = inventory.WINDOWS_EXECUTABLE in actual_set
    if (
        len(actual_set) != len(actual_paths)
        or not inventory.required_runtime_files(windows=windows).issubset(actual_set)
        or any(not inventory.is_runtime_file(path, windows=windows) for path in actual_set)
    ):
        raise errors.InstallError("released payload file set mismatch")
    for blob in blobs:
        if blob.mode not in {"100644", "100755"}:
            raise errors.InstallError(f"released payload mode is invalid: {blob.path}")
        if hashlib.sha256(blob.content).hexdigest() != blob.sha256:
            raise errors.InstallError(f"released payload digest mismatch: {blob.path}")
    manifest = manifest_for(version, blobs, receipt_sha256)
    serving_files = receipt.get("serving_files")
    if (
        not isinstance(serving_files, Sequence)
        or isinstance(serving_files, str)
        or not all(isinstance(path, str) for path in serving_files)
        or tuple(serving_files) != actual_paths
    ):
        raise errors.InstallError("released payload serving file set mismatch")
    if receipt.get("serving_payload_sha256") != manifest["serving_payload_sha256"]:
        raise errors.InstallError("released payload serving identity mismatch")


def manifest_for(
    version: str,
    blobs: tuple[artifact.ArtifactFile, ...],
    receipt_sha256: str,
) -> JsonObject:
    """Build the installed manifest for one validated candidate."""
    digests = {blob.path: blob.sha256 for blob in blobs}
    return projection.manifest_for_digests(version, digests, receipt_sha256)


def reject_unowned_collisions(
    ctx: runtime_context.RuntimeContext,
    previous_owned: AbstractSet[str],
    candidate_paths: AbstractSet[str],
) -> None:
    """Refuse to overwrite a candidate path not owned by the prior projection."""
    install = Path(ctx.payload_dir)
    for relative in candidate_paths | set(owned_files.OWNED_PAYLOAD_METADATA):
        path = owned_files.path(install, relative)
        if relative not in previous_owned and (path.exists() or path.is_symlink()):
            raise errors.InstallError(f"candidate unowned collision: {relative}")


def write_projection(
    ctx: runtime_context.RuntimeContext,
    blobs: tuple[artifact.ArtifactFile, ...],
    version: str,
    receipt: ReadOnlyJsonObject,
    receipt_sha256: str,
) -> None:
    """Write candidate bytes, manifest, and receipt into the install root."""
    install = Path(ctx.payload_dir)
    for blob in blobs:
        target = install.joinpath(*PurePosixPath(blob.path).parts)
        owned_files.write_bytes(
            target,
            blob.content,
            mode=0o755 if blob.mode == "100755" else 0o644,
            root=install,
        )
        if digest.sha256_file(target) != blob.sha256:
            raise errors.InstallError(f"installed payload digest mismatch: {blob.path}")
    owned_files.write_bytes(
        install / inventory.MANIFEST_FILENAME,
        projection.manifest_bytes(manifest_for(version, blobs, receipt_sha256)),
        root=install,
    )
    owned_files.write_bytes(
        install / inventory.RELEASE_RECEIPT_FILENAME,
        digest.canonical_json(thaw_value(receipt)),
        mode=0o600,
        root=install,
    )


def prewarm(ctx: runtime_context.RuntimeContext) -> None:
    """Start the exact installed native executable before listener handoff."""
    import subprocess

    executable = Path(ctx.executable)
    environment = native_process_environment(
        install_root=Path(ctx.payload_dir),
        state_root=Path(ctx.log_dir),
        restart_frozen_runtime=True,
    )
    try:
        completed = subprocess.run(
            [str(executable), serving_roles.PREWARM_MODE],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            env=environment,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise errors.InstallError("native bundle prewarm failed") from exc
    if completed.returncode:
        raise errors.InstallError("native bundle prewarm failed")
