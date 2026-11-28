"""Authentic predecessor qualification and native transition observations."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import control as lifecycle_control
from openai_responses_proxy.lifecycle import records
from tests.serving.handoff.fixtures import wait_until

if TYPE_CHECKING:
    from collections.abc import Mapping
    from concurrent.futures import Future

    from openai_responses_proxy.lifecycle import context as runtime_context


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


def admitted_predecessor(
    tmp_path: Path,
    *,
    previous_asset: Path,
    previous_trust: Path,
    current_version: str,
) -> tuple[str, Path]:
    """Prove a published predecessor's bytes and return its version and executable.

    The predecessor's signed checksums prove its archive. The candidate's admission is not used:
    it refuses a payload of another shape, as an installer must, and the predecessor's own
    installer admits its archive in the journey.
    """
    name = artifact._ARCHIVE.fullmatch(previous_asset.name)
    assert name is not None, previous_asset.name
    checksums = previous_asset.with_name("SHA256SUMS").read_bytes()
    artifact._verify_signature(
        checksums, previous_asset.with_name("SHA256SUMS.sig"), previous_trust
    )
    archive = previous_asset.read_bytes()
    assert artifact._parse_checksums(checksums)[previous_asset.name] == (
        hashlib.sha256(archive).hexdigest()
    )
    previous_version = str(name["version"])
    assert records.version_key(previous_version) < records.version_key(current_version)
    output = tmp_path / "published-predecessor"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        bundle.extractall(output, filter="data")
    executable = (
        output
        / f"{product_identity.PRODUCT_SLUG}-{previous_version}-{name['platform']}"
        / "bin"
        / product_identity.COMMAND_NAME
    )
    assert executable.is_file()
    return previous_version, executable
