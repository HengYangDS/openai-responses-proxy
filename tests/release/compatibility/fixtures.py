"""Authentic predecessor qualification and native transition observations."""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control as lifecycle_control
from openai_responses_proxy.lifecycle import records
from tests.serving.handoff.fixtures import wait_until

if TYPE_CHECKING:
    from collections.abc import Mapping
    from concurrent.futures import Future


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable)
    if value is None:
        pytest.skip(f"{variable} is supplied by the release compatibility session")
    return Path(value).resolve(strict=True)


def _assert_same_runtime_identity(
    command_result: Mapping[str, object],
    observed_status: Mapping[str, object],
) -> None:
    """Prove two observations identify one process serving one payload."""
    command_runtime = command_result.get("runtime")
    observed_runtime = observed_status.get("runtime")
    assert isinstance(command_runtime, dict), command_result
    assert isinstance(observed_runtime, dict), observed_status
    identity_fields = (
        "pid",
        "release",
        "serving_payload_sha256",
        "release_receipt_sha256",
        "payload_manifest_sha256",
    )
    assert {field: command_runtime.get(field) for field in identity_fields} == {
        field: observed_runtime.get(field) for field in identity_fields
    }
    assert observed_runtime.get("accepting") is True


def _wait_for_successor_admission(
    future: Future[dict[str, object]],
    ctx: runtime_context.InstallationContext,
    *,
    predecessor_pid: int,
    successor_release: str,
    timeout_seconds: float,
) -> bool:
    """Release held requests once the handed-off successor admits new ones."""

    def admitting_or_finished() -> bool:
        runtime = lifecycle_control.read_runtime(ctx)
        return (
            isinstance(runtime, dict)
            and runtime.get("pid") != predecessor_pid
            and runtime.get("release") == successor_release
            and runtime.get("accepting") is True
        ) or future.done()

    reached = wait_until(admitting_or_finished, timeout_seconds)
    if future.done():
        future.result()
    return reached


def _materialize_native_bundle(candidate: artifact.VerifiedArtifact, output: Path) -> Path:
    """Materialize exact admitted native executable bytes without its provider manifest."""
    output.mkdir()
    for blob in candidate.peek_blobs():
        if blob.path == "providers.toml":
            continue
        relative = PurePosixPath(blob.path)
        assert relative.parts[0] == "bin"
        target = output.joinpath(*relative.parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.content)
        target.chmod(0o755 if blob.mode == "100755" else 0o644)
    executable = output / product_identity.COMMAND_NAME
    assert executable.is_file()
    return output


def admitted_predecessor(
    tmp_path: Path,
    *,
    previous_asset: Path,
    previous_trust: Path,
    current_version: str,
) -> tuple[artifact.VerifiedArtifact, str, Path]:
    """Admit exact published bytes for the retained installation journey."""
    published_predecessor = artifact.admit(
        previous_asset,
        trust_anchor=previous_trust,
    )
    previous_version = published_predecessor.version
    assert records.version_key(previous_version) < records.version_key(current_version)
    previous_bundle = _materialize_native_bundle(
        published_predecessor,
        tmp_path / "published-predecessor-bundle",
    )
    previous_executable = previous_bundle / product_identity.COMMAND_NAME

    return published_predecessor, previous_version, previous_executable
