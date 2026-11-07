"""Assemble verified native platform outputs into one release asset set."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai_responses_proxy import product_identity
from tools.release.artifact import format as assets
from tools.release.artifact import signing

if TYPE_CHECKING:
    from pathlib import Path


def assemble(inputs: tuple[Path, ...], output: Path) -> dict[str, bytes]:
    """Verify and copy one exact asset pair for every supported platform."""
    if output.exists() and any(output.iterdir()):
        raise assets.AssetError("release asset output directory must be empty")
    discovered: dict[str, bytes] = {}
    for root in inputs:
        for path in root.rglob("*"):
            if not path.is_file() or path.name == assets.CHECKSUM_NAME:
                continue
            if path.name in discovered:
                raise assets.AssetError(f"duplicate release asset: {path.name}")
            discovered[path.name] = path.read_bytes()
    version = _version(discovered)
    expected = assets.release_asset_names(
        version, assets.RELEASE_PLATFORMS, require_signature=False
    ) - {assets.CHECKSUM_NAME}
    if set(discovered) != expected:
        raise assets.AssetError("native platform asset set is incomplete or contains unknown files")
    for platform in assets.RELEASE_PLATFORMS:
        assets.verify_platform_archive(
            discovered[assets.archive_name(version, platform)],
            discovered[assets.manifest_name(platform)],
        )
    release = {**discovered, assets.CHECKSUM_NAME: assets.checksums(discovered)}
    output.mkdir(parents=True, exist_ok=True)
    for name, content in release.items():
        (output / name).write_bytes(content)
    return release


def _digests(root: Path) -> dict[str, str]:
    """Validate one complete signed inventory and return its measured digests."""
    files = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    version = _version(files)
    return assets.release_digests(
        files,
        version,
        assets.RELEASE_PLATFORMS,
    )


def verify(root: Path, *, trust: str) -> dict[str, str]:
    """Authenticate one complete release against explicit external trust."""
    try:
        signing.verify(assets=root, trust=trust)
    except signing.SignatureError as error:
        raise assets.AssetError("release asset signature is invalid") from error
    return _digests(root)


def assemble_sign_verify(
    *, inputs: tuple[Path, ...], output: Path, key: Path, trust: str
) -> dict[str, str]:
    """Assemble, sign, and verify one complete release asset set."""
    if not key.is_file() or key.is_symlink() or not trust.strip():
        raise assets.AssetError("release signing inputs are unavailable")
    assemble(inputs, output)
    try:
        signing.sign_and_verify(assets=output, key=key, trust=trust)
        return _digests(output)
    except signing.SignatureError as error:
        raise assets.AssetError("release asset signature is invalid") from error


def _version(discovered: dict[str, bytes]) -> str:
    versions = {
        name.removeprefix(f"{product_identity.PRODUCT_SLUG}-").removesuffix(f"-{platform}.tar.gz")
        for platform in assets.RELEASE_PLATFORMS
        for name in discovered
        if name.endswith(f"-{platform}.tar.gz")
    }
    if len(versions) != 1:
        raise assets.AssetError("native platform assets do not share one version")
    return versions.pop()
