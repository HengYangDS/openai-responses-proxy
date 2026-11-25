"""Portable asset fixtures shared by construction and publication tests."""

from pathlib import Path

from openai_responses_proxy import product_identity
from tools.release.artifact import format as assets


def release_bundle(
    platforms: tuple[str, ...] = assets.RELEASE_PLATFORMS,
    *,
    version: str = "1.2.3",
) -> dict[str, bytes]:
    platform_assets: dict[str, bytes] = {}
    for platform in platforms:
        files = {
            f"bin/{product_identity.COMMAND_NAME}": assets.ArchiveFile(
                f"native-{platform}".encode(), mode=0o755
            )
        }
        archive_name = assets.archive_name(version, platform)
        archive = assets.archive_bytes(files, version, platform)
        platform_assets[archive_name] = archive
        platform_assets[assets.manifest_name(platform)] = assets.asset_manifest(
            version=version,
            platform=platform,
            archive_name=archive_name,
            archive=archive,
            files=files,
        )
    unsigned = {
        **platform_assets,
        assets.CHECKSUM_NAME: assets.checksums(platform_assets),
    }
    return {**unsigned, assets.SIGNATURE_NAME: b"fixture-signature\n"}


# A platform that releases before RELEASE_PLATFORMS_SINCE also carried.
RETIRED_PLATFORM = "windows-x86_64"


def version_before(version: str) -> str:
    """Return a strict release version below ``version``."""
    major, minor, patch = (int(part) for part in version.split("."))
    if patch:
        return f"{major}.{minor}.{patch - 1}"
    if minor:
        return f"{major}.{minor - 1}.0"
    return f"{major - 1}.0.0"


def native_inputs(root: Path) -> tuple[Path, ...]:
    """Write one complete set of platform build outputs for assembly tests."""
    inputs: list[Path] = []
    for platform in assets.RELEASE_PLATFORMS:
        output = root / platform
        output.mkdir()
        for name, content in release_bundle((platform,)).items():
            if name != assets.SIGNATURE_NAME:
                (output / name).write_bytes(content)
        inputs.append(output)
    return tuple(inputs)
