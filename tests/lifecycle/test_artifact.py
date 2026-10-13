"""Signed native artifact admission and one-use capability contracts."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import pytest
from pytest_mock import MockerFixture

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import runtime_files
from tools.release.artifact import format as product_assets

VERSION = "1.2.3"
PLATFORM = "linux-x86_64"
NAMESPACE = "openai-responses-proxy-release"


def _release_files(
    platform: str = PLATFORM,
) -> dict[str, product_assets.ArchiveFile | bytes]:
    executable = (
        "bin/openai-responses-proxy.exe"
        if platform.startswith("windows-")
        else "bin/openai-responses-proxy"
    )
    return {
        executable: product_assets.ArchiveFile(b"native-executable", 0o755),
        "providers.toml": b"version = 1\n",
        "LICENSE": b"MIT\n",
    }


def _write_asset_set(tmp_path: Path, platform: str = PLATFORM) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    files = _release_files(platform)
    archive_name = product_assets.archive_name(VERSION, platform)
    archive_bytes = product_assets.archive_bytes(files, VERSION, platform)
    manifest_bytes = product_assets.asset_manifest(
        version=VERSION,
        platform=platform,
        archive_name=archive_name,
        archive=archive_bytes,
        files=files,
    )
    archive_path = tmp_path / archive_name
    manifest_path = tmp_path / product_assets.manifest_name(platform)
    checksums_path = tmp_path / product_assets.CHECKSUM_NAME
    signature_path = tmp_path / product_assets.SIGNATURE_NAME
    anchor_path = tmp_path / "allowed-signers"
    archive_path.write_bytes(archive_bytes)
    manifest_path.write_bytes(manifest_bytes)
    checksums_path.write_bytes(
        product_assets.checksums(
            {archive_path.name: archive_bytes, manifest_path.name: manifest_bytes}
        )
    )
    signature_path.write_bytes(b"signature fixture\n")
    anchor_path.write_bytes(b"trust fixture\n")
    return {
        "archive": archive_path,
        "manifest": manifest_path,
        "checksums": checksums_path,
        "signature": signature_path,
        "anchor": anchor_path,
    }


def _sign_asset_set(paths: Mapping[str, Path]) -> None:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("ssh-keygen is required for signed artifact integration")
    root = paths["archive"].parent
    paths["signature"].unlink(missing_ok=True)
    key = root / "release-key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            NAMESPACE,
            paths["checksums"].name,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    key_type, public_key, *_ = key.with_suffix(".pub").read_text(encoding="ascii").split()
    paths["anchor"].write_text(
        f'release@example.test namespaces="{NAMESPACE}" {key_type} {public_key}\n',
        encoding="ascii",
    )


def _plain_receipt(candidate: artifact.VerifiedArtifact) -> dict[str, object]:
    value = artifact.plain_value(candidate.receipt)
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _rebind_receipt(candidate: artifact.VerifiedArtifact, receipt: dict[str, object]) -> None:
    receipt_sha256 = hashlib.sha256(digest.canonical_json(receipt)).hexdigest()
    sidecar = artifact.plain_value(candidate.sidecar)
    assert isinstance(sidecar, dict)
    sidecar.update(
        receipt_sha256=receipt_sha256,
        serving_payload_sha256=receipt.get("serving_payload_sha256"),
    )
    object.__setattr__(candidate, "_receipt", digest.freeze_mapping(receipt))
    object.__setattr__(candidate, "_receipt_sha256", receipt_sha256)
    object.__setattr__(candidate, "_sidecar", digest.freeze_mapping(sidecar))


def _replace_last_blob(
    candidate: artifact.VerifiedArtifact,
    *,
    path: str | None = None,
    mode: Literal["100644", "100755"] | None = None,
    blob_oid: str | None = None,
    sha256: str | None = None,
    content: bytes | None = None,
) -> None:
    blob = candidate.peek_blobs()[-1]
    replacement = artifact.ArtifactFile(
        path=blob.path if path is None else path,
        mode=blob.mode if mode is None else mode,
        blob_oid=blob.blob_oid if blob_oid is None else blob_oid,
        sha256=blob.sha256 if sha256 is None else sha256,
        content=blob.content if content is None else content,
    )
    object.__setattr__(candidate, "_blobs", (*candidate.peek_blobs()[:-1], replacement))


def _receipt_drift(candidate: artifact.VerifiedArtifact) -> None:
    object.__setattr__(candidate, "_receipt", {**candidate.receipt, "version": "9.9.9"})


def _sidecar_drift(candidate: artifact.VerifiedArtifact) -> None:
    object.__setattr__(
        candidate,
        "_sidecar",
        {**candidate.sidecar, "receipt_sha256": "0" * 64},
    )


def _payload_type_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    receipt["payload"] = "invalid"
    _rebind_receipt(candidate, receipt)


def _payload_length_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    payload = receipt["payload"]
    assert isinstance(payload, list)
    receipt["payload"] = payload[:-1]
    _rebind_receipt(candidate, receipt)


def _payload_entry_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    payload = receipt["payload"]
    assert isinstance(payload, list)
    payload[0] = "invalid"
    _rebind_receipt(candidate, receipt)


def _blob_path_drift(candidate: artifact.VerifiedArtifact) -> None:
    _replace_last_blob(candidate, path="wrong")


def _blob_mode_drift(candidate: artifact.VerifiedArtifact) -> None:
    _replace_last_blob(candidate, mode="100755")


def _blob_oid_drift(candidate: artifact.VerifiedArtifact) -> None:
    _replace_last_blob(candidate, blob_oid="0" * 64)


def _blob_sha_drift(candidate: artifact.VerifiedArtifact) -> None:
    _replace_last_blob(candidate, sha256="0" * 64)


def _blob_content_drift(candidate: artifact.VerifiedArtifact) -> None:
    _replace_last_blob(candidate, content=b"tampered")


def _serving_type_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    receipt["serving_files"] = "invalid"
    _rebind_receipt(candidate, receipt)


def _serving_inventory_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    receipt["serving_files"] = [inventory.PROVIDER_MANIFEST]
    _rebind_receipt(candidate, receipt)


def _serving_digest_drift(candidate: artifact.VerifiedArtifact) -> None:
    receipt = _plain_receipt(candidate)
    receipt["serving_payload_sha256"] = "0" * 64
    _rebind_receipt(candidate, receipt)


def _tar_bytes(
    members: tuple[tuple[str, bytes, int, bytes | None], ...],
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, content, mode, member_type in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.type = tarfile.REGTYPE if member_type is None else member_type
            info.size = len(content) if info.isfile() else 0
            bundle.addfile(info, io.BytesIO(content) if info.isfile() else None)
    return output.getvalue()


def _members(
    platform: str = PLATFORM,
) -> tuple[tuple[str, bytes, int, bytes | None], ...]:
    prefix = f"openai-responses-proxy-{VERSION}-{platform}/"
    files = _release_files(platform)
    return tuple(
        (
            f"{prefix}{name}",
            value.content if isinstance(value, product_assets.ArchiveFile) else value,
            value.mode if isinstance(value, product_assets.ArchiveFile) else 0o644,
            None,
        )
        for name, value in files.items()
    )


def test_verified_artifact_is_opaque_immutable_and_single_use() -> None:
    with pytest.raises(TypeError, match="opaque"):
        artifact.VerifiedArtifact(blobs=(), receipt={}, sidecar={})
    for untrusted in (object(), str(Path(__file__)), Path(__file__)):
        with pytest.raises(artifact.ArtifactError, match="admitted"):
            artifact.claim(untrusted)

    candidate = released_artifact()
    assert candidate.version == VERSION
    assert candidate.serving_payload_sha256 == candidate.receipt["serving_payload_sha256"]
    assert (
        candidate.receipt_sha256
        == hashlib.sha256(digest.canonical_json(_plain_receipt(candidate))).hexdigest()
    )
    assert candidate.peek_blobs()
    with pytest.raises(artifact.ArtifactError, match="immutable"):
        candidate._claimed = False

    claimed = artifact.claim(candidate)
    assert tuple(blob.path for blob in claimed[0]) == runtime_files()
    with pytest.raises(artifact.ArtifactError, match="already claimed"):
        artifact.claim(candidate)


@pytest.mark.parametrize(
    "mutate",
    [
        _receipt_drift,
        _sidecar_drift,
        _payload_type_drift,
        _payload_length_drift,
        _payload_entry_drift,
        _blob_path_drift,
        _blob_mode_drift,
        _blob_oid_drift,
        _blob_sha_drift,
        _blob_content_drift,
        _serving_type_drift,
        _serving_inventory_drift,
        _serving_digest_drift,
    ],
)
def test_claim_revalidates_every_receipt_sidecar_and_blob_binding(
    mutate: Callable[[artifact.VerifiedArtifact], None],
) -> None:
    candidate = released_artifact()
    mutate(candidate)
    with pytest.raises(artifact.ArtifactError, match="integrity"):
        artifact.claim(candidate)


def test_failed_integrity_check_does_not_consume_authority() -> None:
    candidate = released_artifact()
    original = candidate.sidecar
    _sidecar_drift(candidate)
    with pytest.raises(artifact.ArtifactError):
        artifact.claim(candidate)
    object.__setattr__(candidate, "_sidecar", original)
    assert tuple(blob.path for blob in artifact.claim(candidate)[0]) == runtime_files()


def test_plain_value_recursively_unfreezes_json_values() -> None:
    assert artifact.plain_value(({1: ("value",)},)) == [{"1": ["value"]}]


def test_real_signature_admission_mints_exact_native_artifact(tmp_path: Path) -> None:
    paths = _write_asset_set(tmp_path)
    _sign_asset_set(paths)

    candidate = artifact.admit(paths["archive"], trust_anchor=paths["anchor"])

    assert candidate.version == VERSION
    assert tuple(blob.path for blob in candidate.peek_blobs()) == runtime_files()
    assert candidate.receipt["verification_scope"] == "signed-native-release-asset"
    assert candidate.receipt["archive"] == paths["archive"].name
    assert candidate.sidecar["receipt_sha256"] == candidate.receipt_sha256


@pytest.mark.parametrize("missing", ["manifest", "checksums", "signature"])
def test_admission_requires_every_companion_asset(
    tmp_path: Path, mocker: MockerFixture, missing: str
) -> None:
    paths = _write_asset_set(tmp_path)
    paths[missing].unlink()
    mocker.patch.object(artifact, "_verify_signature")
    with pytest.raises(errors.InstallError, match="unavailable"):
        artifact.admit(paths["archive"], trust_anchor=paths["anchor"])


def test_admission_rejects_nonfile_oversized_and_invalid_name(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    with pytest.raises(errors.InstallError, match="archive is unavailable"):
        artifact.admit(tmp_path, trust_anchor=tmp_path)

    invalid = tmp_path / "native.tar.gz"
    invalid.write_bytes(b"archive")
    with pytest.raises(errors.InstallError, match="name is invalid"):
        artifact.admit(invalid, trust_anchor=invalid)

    paths = _write_asset_set(tmp_path / "oversized")
    mocker.patch.object(artifact, "_MAX_ASSET_BYTES", 1)
    with pytest.raises(errors.InstallError, match="too large"):
        artifact.admit(paths["archive"], trust_anchor=paths["anchor"])


def test_admission_accepts_selected_assets_from_complete_signed_release(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    paths = _write_asset_set(tmp_path)
    extra = {
        "openai-responses-proxy-1.2.3-macos-arm64.tar.gz": b"macos archive",
        "openai-responses-proxy-macos-arm64.manifest.json": b"macos manifest",
    }
    paths["checksums"].write_bytes(
        product_assets.checksums(
            {
                paths["archive"].name: paths["archive"].read_bytes(),
                paths["manifest"].name: paths["manifest"].read_bytes(),
                **extra,
            }
        )
    )
    mocker.patch.object(artifact, "_verify_signature")

    candidate = artifact.admit(paths["archive"], trust_anchor=paths["anchor"])

    assert candidate.version == VERSION
    assert candidate.receipt["archive"] == paths["archive"].name


def test_admission_requires_exact_signed_checksum_inventory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    paths = _write_asset_set(tmp_path)
    paths["checksums"].write_bytes(b"0" * 64 + b"  unknown\n")
    mocker.patch.object(artifact, "_verify_signature")
    with pytest.raises(errors.InstallError, match="do not match"):
        artifact.admit(paths["archive"], trust_anchor=paths["anchor"])


def test_signature_verification_fails_closed_at_each_external_boundary(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    signature = tmp_path / "signature"
    anchor = tmp_path / "anchor"
    signature.write_bytes(b"signature")
    anchor.write_bytes(b"anchor")

    mocker.patch.object(artifact.shutil, "which", return_value=None)
    with pytest.raises(errors.InstallError, match="ssh-keygen is required"):
        artifact._verify_signature(b"content", signature, anchor)

    mocker.patch.object(artifact.shutil, "which", return_value="/usr/bin/ssh-keygen")
    run = mocker.patch.object(
        artifact.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"denied"),
    )
    with pytest.raises(errors.InstallError, match="unique authorized principal"):
        artifact._verify_signature(b"content", signature, anchor)

    run.side_effect = (subprocess.CompletedProcess([], 0, stdout=b"one\ntwo\n", stderr=b""),)
    with pytest.raises(errors.InstallError, match="unique authorized principal"):
        artifact._verify_signature(b"content", signature, anchor)

    run.side_effect = (
        subprocess.CompletedProcess([], 0, stdout=b"release@example.test\n", stderr=b""),
        subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"denied"),
    )
    with pytest.raises(errors.InstallError, match="verification failed"):
        artifact._verify_signature(b"content", signature, anchor)

    verify_command = run.call_args_list[-1].args[0]
    assert verify_command[verify_command.index("-I") + 1] == "release@example.test"
    assert verify_command[verify_command.index("-n") + 1] == NAMESPACE


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"invalid\n",
        b"0" * 64 + b"  ../escape\n",
        b"0" * 64 + b"  asset\n" + b"1" * 64 + b"  asset\n",
    ],
)
def test_checksum_parser_rejects_noncanonical_content(content: bytes) -> None:
    with pytest.raises(errors.InstallError, match="malformed"):
        artifact._parse_checksums(content)


def test_platform_manifest_binds_exact_archive_identity(tmp_path: Path) -> None:
    paths = _write_asset_set(tmp_path)
    document = json.loads(paths["manifest"].read_bytes())
    document["archive"] = "other.tar.gz"
    with pytest.raises(errors.InstallError, match="inconsistent"):
        artifact._verify_archive(
            paths["archive"].read_bytes(),
            json.dumps(document).encode(),
            {"version": VERSION, "platform": PLATFORM},
        )


@pytest.mark.parametrize(
    "manifest",
    [
        b"\xff",
        b"not-json",
        b"[]",
        b"{}",
    ],
)
def test_platform_manifest_rejects_malformed_or_incomplete_json(
    tmp_path: Path, manifest: bytes
) -> None:
    paths = _write_asset_set(tmp_path)
    with pytest.raises(errors.InstallError, match=r"malformed|inconsistent"):
        artifact._verify_archive(
            paths["archive"].read_bytes(),
            manifest,
            {"version": VERSION, "platform": PLATFORM},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("product", "other"),
        ("version", "9.9.9"),
        ("platform", "other-platform"),
        ("archive_sha256", "0" * 64),
        ("files", []),
    ],
)
def test_platform_manifest_rejects_inconsistent_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    paths = _write_asset_set(tmp_path)
    document = json.loads(paths["manifest"].read_bytes())
    document[field] = value
    with pytest.raises(errors.InstallError, match="inconsistent"):
        artifact._verify_archive(
            paths["archive"].read_bytes(),
            json.dumps(document).encode(),
            {"version": VERSION, "platform": PLATFORM},
        )


def test_archive_rejects_incomplete_duplicate_and_unexpected_members(
    tmp_path: Path,
) -> None:
    paths = _write_asset_set(tmp_path)
    document = json.loads(paths["manifest"].read_bytes())
    members = _members()

    with pytest.raises(errors.InstallError, match="inventory"):
        artifact._archive_blobs(_tar_bytes(members[:-1]), document)

    duplicate = (members[0], members[0], members[2])
    with pytest.raises(errors.InstallError, match=r"invalid member|inventory"):
        artifact._archive_blobs(_tar_bytes(duplicate), document)

    prefix = f"openai-responses-proxy-{VERSION}-{PLATFORM}/"
    unexpected = (members[0], members[1], (f"{prefix}other", b"MIT\n", 0o644, None))
    with pytest.raises(errors.InstallError, match="invalid member"):
        artifact._archive_blobs(_tar_bytes(unexpected), document)


def test_archive_rejects_member_type_digest_mode_and_corruption(tmp_path: Path) -> None:
    paths = _write_asset_set(tmp_path)
    document = json.loads(paths["manifest"].read_bytes())
    members = list(_members())

    invalid_type = list(members)
    name, content, mode, _ = invalid_type[0]
    invalid_type[0] = (name, content, mode, tarfile.DIRTYPE)
    with pytest.raises(errors.InstallError, match="invalid member"):
        artifact._archive_blobs(_tar_bytes(tuple(invalid_type)), document)

    digest_drift = list(members)
    name, _, mode, kind = digest_drift[0]
    digest_drift[0] = (name, b"tampered", mode, kind)
    with pytest.raises(errors.InstallError, match="digest mismatch"):
        artifact._archive_blobs(_tar_bytes(tuple(digest_drift)), document)

    mode_drift = list(members)
    name, content, _, kind = mode_drift[0]
    mode_drift[0] = (name, content, 0o644, kind)
    with pytest.raises(errors.InstallError, match="mode is invalid"):
        artifact._archive_blobs(_tar_bytes(tuple(mode_drift)), document)

    with pytest.raises(errors.InstallError, match="malformed"):
        artifact._archive_blobs(b"not-a-tar", document)


def test_archive_requires_exact_declared_inventory(tmp_path: Path) -> None:
    paths = _write_asset_set(tmp_path)
    document = json.loads(paths["manifest"].read_bytes())
    document["files"].pop("LICENSE")
    with pytest.raises(errors.InstallError, match="inventory"):
        artifact._archive_blobs(paths["archive"].read_bytes(), document)
