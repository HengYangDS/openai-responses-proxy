"""Package one accepted native executable into a manifest-bound release asset."""

from __future__ import annotations

import csv
import io
import os
import platform
import tarfile
from pathlib import Path

from openai_responses_proxy import product_identity
from tools.release.artifact import format as assets

ROOT = Path(__file__).resolve().parents[3]
INSTALLER_PROVENANCE = frozenset({"direct_url.json", "uv_cache.json"})


def normalize(packages: Path) -> None:
    """Remove installer-local product metadata before executable freezing."""
    metadata = tuple(packages.glob("openai_responses_proxy-*.dist-info"))
    if len(metadata) != 1 or not metadata[0].is_dir() or metadata[0].is_symlink():
        raise RuntimeError("installed product distribution metadata is ambiguous")
    record = metadata[0] / "RECORD"
    if not record.is_file() or record.is_symlink():
        raise RuntimeError("installed product distribution inventory is unavailable")
    relative_metadata = metadata[0].relative_to(packages).as_posix()
    provenance = {f"{relative_metadata}/{name}" for name in INSTALLER_PROVENANCE}
    with record.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    names = {row[0] for row in rows if len(row) == 3}
    if any(len(row) != 3 for row in rows) or not provenance <= names:
        raise RuntimeError("installed product distribution inventory is malformed")
    for name in provenance:
        path = packages.joinpath(*Path(name).parts)
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("installed product provenance metadata is unavailable")
        path.unlink()
    with record.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerows(row for row in rows if row[0] not in provenance)


def _is_within(bundle: Path, member: Path) -> bool:
    """Return whether two resolved paths share the host's bundle identity."""
    canonical_bundle = os.path.normcase(str(bundle))
    canonical_member = os.path.normcase(str(member))
    shared = os.path.normcase(os.path.commonpath((canonical_bundle, canonical_member)))
    return shared == canonical_bundle


def bundle_files(bundle: Path) -> tuple[tuple[Path, Path], ...]:
    """Return logical bundle files after safely materializing internal links."""
    files: list[tuple[Path, Path]] = []

    def visit(logical: Path, source: Path, ancestors: frozenset[Path]) -> None:
        try:
            resolved = source.resolve(strict=True)
            if not _is_within(bundle, resolved):
                raise ValueError
        except (OSError, ValueError) as error:
            raise SystemExit("native bundle symlink escapes the bundle") from error
        if resolved.is_file():
            if logical.parent.name.endswith(".dist-info") and logical.name in INSTALLER_PROVENANCE:
                return
            files.append((logical, resolved))
            return
        if not resolved.is_dir():
            raise SystemExit("native bundle contains a non-regular member")
        if resolved in ancestors:
            raise SystemExit("native bundle contains a symlink cycle")
        descendants = ancestors | {resolved}
        for child in sorted(resolved.iterdir(), key=lambda path: path.name):
            visit(logical / child.name, child, descendants)

    for child in sorted(bundle.iterdir(), key=lambda path: path.name):
        visit(Path(child.name), child, frozenset({bundle}))
    return tuple(files)


def pack(*, bundle: Path, platform: str, output: Path) -> None:
    """Write one platform archive, machine manifest, and checksum manifest."""
    if output.exists() and any(output.iterdir()):
        raise SystemExit("release asset output directory must be empty")
    bundle = bundle.resolve(strict=True)
    if not bundle.is_dir():
        raise SystemExit("native bundle must be a directory")
    version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
    executable_name = product_identity.executable_name(windows=platform.startswith("windows-"))
    executable = bundle / executable_name
    if not executable.is_file() or executable.is_symlink():
        raise SystemExit("native bundle executable is unavailable")
    files: dict[str, bytes | assets.ArchiveFile] = {
        f"bin/{executable_name}": assets.ArchiveFile(executable.read_bytes(), 0o755),
        "providers.toml": (
            ROOT / "src/openai_responses_proxy/providers/manifest.toml"
        ).read_bytes(),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
    }
    for relative, source in bundle_files(bundle):
        if relative == Path(executable_name):
            continue
        files[f"bin/{relative.as_posix()}"] = source.read_bytes()
    archive_name = assets.archive_name(version, platform)
    archive = assets.archive_bytes(files, version, platform)
    manifest_name = assets.manifest_name(platform)
    manifest = assets.asset_manifest(
        version=version,
        platform=platform,
        archive_name=archive_name,
        archive=archive,
        files=files,
    )
    release_files = {archive_name: archive, manifest_name: manifest}
    checksums = assets.checksums(release_files)
    output.mkdir(parents=True, exist_ok=True)
    for name, content in {**release_files, assets.CHECKSUM_NAME: checksums}.items():
        (output / name).write_bytes(content)
    assets.release_digests(
        {**release_files, assets.CHECKSUM_NAME: checksums},
        version,
        (platform,),
        require_signature=False,
    )
    print(f"release assets: {archive_name} {manifest_name} {assets.CHECKSUM_NAME} OK")


def materialize(*, source: Path, output: Path) -> None:
    """Verify one workflow-owned native asset set before exposing its exact bytes."""
    if source.is_symlink() or not source.is_dir():
        raise SystemExit("candidate native asset directory is unavailable")
    if output.exists() or output.is_symlink():
        raise SystemExit("candidate materialization requires a fresh output directory")
    members = tuple(source.iterdir())
    if not members or any(path.is_symlink() or not path.is_file() for path in members):
        raise SystemExit("candidate native asset inventory is invalid")
    version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
    native_platform = product_identity.native_release_platform(
        platform.system(), platform.machine()
    )
    files = {path.name: path.read_bytes() for path in members}
    assets.release_digests(files, version, (native_platform,), require_signature=False)
    archive = files[assets.archive_name(version, native_platform)]
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as archive_input:
        prefix = f"{product_identity.PRODUCT_SLUG}-{version}-{native_platform}/"
        output.mkdir(parents=True)
        for member in archive_input.getmembers():
            member.name = member.name.removeprefix(prefix)
            archive_input.extract(member, output, filter="data")
    print("candidate native assets: verified and materialized")
