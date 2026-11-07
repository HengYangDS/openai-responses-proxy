"""Contracts for deterministic, checksum-bound release assets."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from openai_responses_proxy import product_identity
from tests.release.artifact.fixtures import release_bundle
from tools.release.artifact import format as assets


class FormatContracts:
    """Verify the format owner of native release artifacts."""

    @pytest.mark.parametrize(
        ("system", "machine", "expected"),
        [
            ("Darwin", "arm64", "macos-arm64"),
            ("Linux", "x86_64", "linux-x86_64"),
            ("Windows", "AMD64", "windows-x86_64"),
        ],
    )
    def test_native_platform_identity_has_one_release_owner(
        self, system: str, machine: str, expected: str
    ) -> None:
        """Map native hosts through the release platform inventory once."""
        assert product_identity.native_release_platform(system, machine) == expected

    @pytest.mark.parametrize(
        ("system", "machine"),
        [("Darwin", "x86_64"), ("Linux", "riscv64"), ("Plan9", "x86_64")],
    )
    def test_native_platform_identity_rejects_unreleased_hosts(
        self, system: str, machine: str
    ) -> None:
        """Reject host identities absent from the release inventory."""
        with pytest.raises(ValueError, match="unsupported native release platform"):
            product_identity.native_release_platform(system, machine)

    def test_platform_archive_is_reproducible_and_manifest_bound(self) -> None:
        files = {
            "bin/openai-responses-proxy": assets.ArchiveFile(b"native-executable", 0o755),
            "providers.toml": b"version = 1\n",
            "LICENSE": b"MIT\n",
        }
        first = assets.archive_bytes(files, "1.2.3", "linux-x86_64")
        second = assets.archive_bytes(dict(reversed(tuple(files.items()))), "1.2.3", "linux-x86_64")
        assert first == second
        archive_name = assets.archive_name("1.2.3", "linux-x86_64")
        manifest = assets.asset_manifest(
            version="1.2.3",
            platform="linux-x86_64",
            archive_name=archive_name,
            archive=first,
            files=files,
        )
        decoded = json.loads(manifest)
        assert decoded["schema_version"] == 1
        assert decoded["version"] == "1.2.3"
        assert decoded["platform"] == "linux-x86_64"
        assert decoded["archive"] == archive_name
        assert set(decoded["files"]) == set(files)
        assets.verify_platform_archive(first, manifest)
        with pytest.raises(assets.AssetError):
            assets.verify_platform_archive(first + b"drift", manifest)

    def test_checksum_manifest_round_trips_and_rejects_drift(self, subtests) -> None:
        platform = "linux-x86_64"
        files = {"bin/openai-responses-proxy": assets.ArchiveFile(b"native", 0o755)}
        archive_name = assets.archive_name("1.2.3", platform)
        archive = assets.archive_bytes(files, "1.2.3", platform)
        payload = {
            archive_name: archive,
            assets.manifest_name(platform): assets.asset_manifest(
                version="1.2.3",
                platform=platform,
                archive_name=archive_name,
                archive=archive,
                files=files,
            ),
        }
        manifest = assets.checksums(payload)
        expected = hashlib.sha256(archive).hexdigest()
        assert assets.verify(payload, manifest)[archive_name] == expected
        release_files = {
            **payload,
            assets.CHECKSUM_NAME: manifest,
            assets.SIGNATURE_NAME: b"sig",
        }
        assert set(assets.release_digests(release_files, "1.2.3", ("linux-x86_64",))) == {
            next(iter(payload)),
            assets.manifest_name("linux-x86_64"),
            assets.CHECKSUM_NAME,
            assets.SIGNATURE_NAME,
        }
        for changed, checksum in (
            ({"wrong": b"archive"}, manifest),
            (payload, b"bad\n"),
        ):
            with (
                subtests.test(changed=changed, checksum=checksum),
                pytest.raises(assets.AssetError),
            ):
                assets.verify(changed, checksum)
        with pytest.raises(assets.AssetError):
            assets.release_digests(
                {**release_files, "unexpected": b"x"},
                "1.2.3",
                ("linux-x86_64",),
            )

    def test_invalid_paths_and_manifests_fail_closed(self, subtests) -> None:
        with pytest.raises(assets.AssetError):
            assets.checksums({})
        for path in ("../escape", "..\\escape", "/absolute", "C:\\absolute"):
            with subtests.test(path=path), pytest.raises(assets.AssetError):
                assets.archive_bytes({path: b"x"}, "1.2.3", "linux-x86_64")
        for manifest in (
            b"",
            b"\xff",
            b"not-a-digest  asset\n",
            b"0" * 64 + b"  ../escape\n",
            b"0" * 64 + b"  a\n" + b"1" * 64 + b"  a\n",
        ):
            with subtests.test(manifest=manifest), pytest.raises(assets.AssetError):
                assets.parse_checksums(manifest)


@pytest.mark.parametrize("version", [True, 1.0, 2])
def test_manifest_schema_uses_exact_integer_identity(version) -> None:
    platform = assets.RELEASE_PLATFORMS[0]
    files = release_bundle((platform,))
    manifest = json.loads(files[assets.manifest_name(platform)])
    manifest["schema_version"] = version
    with pytest.raises(assets.AssetError, match="manifest"):
        assets.verify_platform_archive(
            files[assets.archive_name("1.2.3", platform)], json.dumps(manifest).encode()
        )


@pytest.mark.parametrize("manifest", [b"not-json", b"[]", b"{}"])
def test_manifest_requires_one_complete_object(manifest) -> None:
    with pytest.raises(assets.AssetError, match="manifest is malformed"):
        assets.verify_platform_archive(b"", manifest)


@pytest.mark.parametrize(
    ("field", "value"), [("version", 1), ("platform", None), ("files", {"../outside": "0" * 64})]
)
def test_manifest_requires_portable_typed_inventory(field, value) -> None:
    platform = assets.RELEASE_PLATFORMS[0]
    files = release_bundle((platform,))
    manifest = json.loads(files[assets.manifest_name(platform)])
    manifest[field] = value
    with pytest.raises(assets.AssetError, match="manifest"):
        assets.verify_platform_archive(
            files[assets.archive_name("1.2.3", platform)], json.dumps(manifest).encode()
        )


@pytest.mark.parametrize(
    "defect", ["directory", "outside", "duplicate", "mode", "digest", "corrupt"]
)
def test_archive_members_are_bound_to_portable_manifest(defect) -> None:
    platform = assets.RELEASE_PLATFORMS[0]
    files = release_bundle((platform,))
    manifest = json.loads(files[assets.manifest_name(platform)])
    name = next(iter(manifest["files"]))
    prefix = f"{product_identity.PRODUCT_SLUG}-1.2.3-{platform}/"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(("wrong/" if defect == "outside" else prefix) + name)
        member.mode = 0o777 if defect == "mode" else 0o755
        member.type = tarfile.DIRTYPE if defect == "directory" else tarfile.REGTYPE
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
        if defect == "duplicate":
            archive.addfile(member, io.BytesIO(b"x"))
    content = b"corrupt" if defect == "corrupt" else buffer.getvalue()
    manifest["archive_sha256"] = hashlib.sha256(content).hexdigest()
    with pytest.raises(assets.AssetError):
        assets.verify_platform_archive(content, json.dumps(manifest).encode())


@pytest.mark.parametrize("platforms", [(), ("linux-x86_64", "linux-x86_64")])
def test_platform_inventory_has_one_nonempty_identity(platforms) -> None:
    with pytest.raises(assets.AssetError, match="unique and nonempty"):
        assets.release_asset_names("1.2.3", platforms)
    with pytest.raises(assets.AssetError, match="unique and nonempty"):
        assets.release_digests({}, "1.2.3", platforms)


def test_asset_paths_modes_and_names_must_be_canonical() -> None:
    with pytest.raises(assets.AssetError, match="mode is invalid"):
        assets.archive_bytes({"file": assets.ArchiveFile(b"x", 0o777)}, "1.2.3", "linux-x86_64")
    with pytest.raises(assets.AssetError, match="identity is invalid"):
        assets.asset_manifest(
            version="1.2.3", platform="linux-x86_64", archive_name="wrong", archive=b"x", files={}
        )
    with pytest.raises(assets.AssetError, match="path escapes"):
        assets.asset_manifest(
            version="1.2.3",
            platform="linux-x86_64",
            archive_name=assets.archive_name("1.2.3", "linux-x86_64"),
            archive=b"x",
            files={"../escape": b"x"},
        )
    with pytest.raises(assets.AssetError, match="identity is invalid"):
        assets.archive_name("not-version", "linux-x86_64")
    with pytest.raises(assets.AssetError, match="incomplete"):
        assets.release_platforms(set(), "1.2.3")
