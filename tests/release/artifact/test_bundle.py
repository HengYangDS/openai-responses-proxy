"""Contracts for deterministic, checksum-bound release assets."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import tempfile
from pathlib import Path

import pytest

from tools.release.artifact import __main__ as commands
from tools.release.artifact import bundle as asset_command
from tools.release.artifact import format as assets

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "defect", ["missing-distribution", "missing-record", "malformed-record", "missing-provenance"]
)
def test_normalization_requires_complete_installed_metadata(tmp_path, defect):
    if defect != "missing-distribution":
        _installed_distribution(tmp_path, "isolated")
        metadata = next(tmp_path.glob("*.dist-info"))
        if defect == "missing-record":
            (metadata / "RECORD").unlink()
        elif defect == "malformed-record":
            (metadata / "RECORD").write_text("invalid\n")
        else:
            (metadata / "direct_url.json").unlink()
    with pytest.raises(RuntimeError, match=r"distribution|provenance"):
        asset_command.normalize(tmp_path)


def test_bundle_rejects_directory_symlink_cycles(tmp_path):
    (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SystemExit, match="symlink cycle"):
        asset_command.bundle_files(tmp_path)


@pytest.mark.parametrize("defect", ["occupied-output", "file-input", "missing-executable"])
def test_pack_rejects_invalid_roots_before_writing(tmp_path, defect):
    bundle = tmp_path / "bundle"
    output = tmp_path / "output"
    if defect == "file-input":
        bundle.touch()
    else:
        bundle.mkdir()
    if defect == "occupied-output":
        output.mkdir()
        (output / "preserved").write_bytes(b"owned")
    with pytest.raises(SystemExit):
        asset_command.pack(bundle=bundle, platform="linux-x86_64", output=output)
    if defect == "occupied-output":
        assert (output / "preserved").read_bytes() == b"owned"
    else:
        assert not output.exists()


def _installed_distribution(root: Path, provenance: str) -> Path:
    """Create one installed product distribution with local installer metadata."""
    metadata = root / "openai_responses_proxy-2.0.30.dist-info"
    package = root / "openai_responses_proxy"
    metadata.mkdir(parents=True)
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "2.0.30"\n', encoding="utf-8")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: openai-responses-proxy\nVersion: 2.0.30\n",
        encoding="utf-8",
    )
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": f"file://{provenance}/product.whl"}), encoding="utf-8"
    )
    (metadata / "uv_cache.json").write_text(
        json.dumps({"timestamp": {"secs_since_epoch": len(provenance)}}),
        encoding="utf-8",
    )
    rows = [
        "openai_responses_proxy/__init__.py,,",
        "openai_responses_proxy-2.0.30.dist-info/METADATA,,",
        "openai_responses_proxy-2.0.30.dist-info/direct_url.json,,",
        "openai_responses_proxy-2.0.30.dist-info/uv_cache.json,,",
        "openai_responses_proxy-2.0.30.dist-info/RECORD,,",
    ]
    (metadata / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


class BundleContracts:
    """Verify the bundle owner of native release artifacts."""

    def test_native_bundle_collects_ctypes_as_source(self) -> None:
        """Execute the shipped hook that keeps ctypes outside the PYZ archive."""
        hook = runpy.run_path(str(ROOT / "tools" / "release" / "hooks" / "hook-ctypes.py"))
        assert hook["module_collection_mode"] == "py"

    def test_bundle_rejects_installer_provenance(self, tmp_path: Path) -> None:
        """Exclude checkout paths and installer timestamps from release payloads."""
        bundle = tmp_path / "openai-responses-proxy"
        metadata = bundle / "_internal" / "openai_responses_proxy-2.0.25.dist-info"
        metadata.mkdir(parents=True)
        (bundle / "openai-responses-proxy").write_bytes(b"native")
        (metadata / "direct_url.json").write_text(
            '{"url":"file:///private/build/checkout/product.whl"}', encoding="utf-8"
        )
        (metadata / "uv_cache.json").write_text(
            '{"timestamp":{"secs_since_epoch":1}}', encoding="utf-8"
        )

        files = dict(asset_command.bundle_files(bundle))

        assert not any(path.name in {"direct_url.json", "uv_cache.json"} for path in files)

    def test_distinct_checkout_roots_produce_identical_archives(self, tmp_path: Path) -> None:
        """Prove checkout-local installer provenance cannot perturb release bytes."""
        archives = []
        for index, checkout in enumerate(("gitlab-build", "github-runner")):
            bundle = tmp_path / checkout / "openai-responses-proxy"
            metadata = bundle / "_internal" / "openai_responses_proxy-2.0.26.dist-info"
            metadata.mkdir(parents=True)
            (bundle / "openai-responses-proxy").write_bytes(b"native")
            (metadata / "METADATA").write_bytes(b"Name: openai-responses-proxy\n")
            (metadata / "direct_url.json").write_text(
                f'{{"url":"file://{bundle}/wheelhouse/product.whl"}}', encoding="utf-8"
            )
            (metadata / "uv_cache.json").write_text(
                f'{{"timestamp":{{"secs_since_epoch":{index + 1}}}}}', encoding="utf-8"
            )
            files = {
                path.as_posix(): source.read_bytes()
                for path, source in asset_command.bundle_files(bundle)
            }
            archives.append(assets.archive_bytes(files, "2.0.26", "linux-x86_64"))

        assert archives[0] == archives[1]

    def test_native_freeze_input_discards_installer_provenance(self, tmp_path: Path) -> None:
        """Normalize metadata before it can alter the frozen executable."""
        first = _installed_distribution(tmp_path / "github", "/workspace/github")
        second = _installed_distribution(tmp_path / "gitlab", "/builds/gitlab")

        commands.main(("normalize", "--packages", str(first)))
        commands.main(("normalize", "--packages", str(second)))

        def snapshot(root: Path) -> dict[str, bytes]:
            return {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }

        assert snapshot(first) == snapshot(second)
        assert not any(
            path.name in {"direct_url.json", "uv_cache.json"} for path in first.rglob("*")
        )

    def test_asset_command_packages_only_native_runtime_inputs(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "openai-responses-proxy"
            executable = bundle / "openai-responses-proxy"
            dependency = bundle / "_internal" / "runtime.dat"
            dependency.parent.mkdir(parents=True)
            executable.write_bytes(b"native-executable")
            dependency.write_bytes(b"frozen-runtime")
            output = root / "release"
            mocker.patch.object(asset_command, "ROOT", ROOT)
            mocker.patch(
                "sys.argv",
                [
                    "artifact",
                    "pack",
                    "--bundle",
                    str(bundle),
                    "--platform",
                    "linux-x86_64",
                    "--output",
                    str(output),
                ],
            )
            commands.main()
            archive_name = assets.archive_name(
                (ROOT / "VERSION").read_text(encoding="ascii").strip(),
                "linux-x86_64",
            )
            expected = {
                archive_name,
                assets.manifest_name("linux-x86_64"),
                assets.CHECKSUM_NAME,
            }
            assert {path.name for path in output.iterdir()} == expected
            release_files = {path.name: path.read_bytes() for path in output.iterdir()}
            manifest = json.loads(release_files[assets.manifest_name("linux-x86_64")])
            assert set(manifest["files"]) == {
                "LICENSE",
                "bin/_internal/runtime.dat",
                "bin/openai-responses-proxy",
                "providers.toml",
            }
            assets.release_digests(
                release_files,
                (ROOT / "VERSION").read_text(encoding="ascii").strip(),
                ("linux-x86_64",),
                require_signature=False,
            )

    @pytest.mark.skipif(os.name == "nt", reason="models POSIX bundle symlink semantics")
    def test_asset_command_materializes_safe_bundle_symlinks(self, tmp_path: Path, mocker) -> None:
        bundle = tmp_path / "openai-responses-proxy"
        framework = bundle / "_internal" / "Python.framework" / "Versions" / "3.14"
        framework.mkdir(parents=True)
        executable = bundle / "openai-responses-proxy"
        executable.write_bytes(b"native-executable")
        runtime = framework / "Python"
        runtime.write_bytes(b"python-runtime")
        (bundle / "_internal" / "Python").symlink_to("Python.framework/Versions/3.14/Python")
        resources = framework / "Resources"
        resources.mkdir()
        (resources / "Info.plist").write_bytes(b"framework-resources")
        (bundle / "_internal" / "Python.framework" / "Resources").symlink_to(
            "Versions/3.14/Resources"
        )
        output = tmp_path / "release"
        mocker.patch.object(asset_command, "ROOT", ROOT)

        asset_command.pack(bundle=bundle, platform="macos-arm64", output=output)

        manifest = json.loads((output / assets.manifest_name("macos-arm64")).read_bytes())
        runtime_digest = hashlib.sha256(b"python-runtime").hexdigest()
        assert manifest["files"]["bin/_internal/Python"] == runtime_digest
        assert manifest["files"]["bin/_internal/Python.framework/Versions/3.14/Python"] == (
            runtime_digest
        )
        assert manifest["files"]["bin/_internal/Python.framework/Resources/Info.plist"] == (
            hashlib.sha256(b"framework-resources").hexdigest()
        )

    def test_bundle_files_uses_platform_canonical_path_identity(self, mocker) -> None:
        bundle = Path("C:/Product/Proxy")
        member = Path("c:/product/proxy/_internal/runtime.dat")
        commonpath = os.path.commonpath
        mocker.patch("os.path.normcase", lambda value: value.casefold().replace("\\", "/"))
        mocker.patch(
            "os.path.commonpath",
            lambda paths: (
                "c:\\product\\proxy"
                if tuple(paths) == ("c:/product/proxy", "c:/product/proxy/_internal/runtime.dat")
                else commonpath(paths)
            ),
        )

        assert asset_command._is_within(bundle, member)

    def test_asset_command_rejects_bundle_symlinks_outside_the_bundle(
        self, tmp_path: Path, mocker
    ) -> None:
        bundle = tmp_path / "openai-responses-proxy"
        bundle.mkdir()
        (bundle / "openai-responses-proxy").write_bytes(b"native-executable")
        outside = tmp_path / "outside"
        outside.write_bytes(b"private")
        (bundle / "escape").symlink_to(outside)
        mocker.patch.object(asset_command, "ROOT", ROOT)

        with pytest.raises(SystemExit, match="escapes"):
            asset_command.pack(
                bundle=bundle,
                platform="macos-arm64",
                output=tmp_path / "release",
            )


@pytest.mark.parametrize(
    "defect",
    [None, "archive", "version", "platform", "renamed-version", "renamed-platform", "output"],
)
def test_materialization_uses_only_complete_current_platform_assets(tmp_path, mocker, defect):
    source = tmp_path / "assets"
    source.mkdir()
    output = tmp_path / "runtime"
    version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
    platform = "windows-x86_64" if os.name == "nt" else "macos-arm64"
    mocker.patch("platform.system", return_value="Windows" if os.name == "nt" else "Darwin")
    mocker.patch("platform.machine", return_value="AMD64" if os.name == "nt" else "arm64")
    executable = "openai-responses-proxy.exe" if os.name == "nt" else "openai-responses-proxy"
    selected_version = "0.0.0" if defect in {"version", "renamed-version"} else version
    selected_platform = "linux-x86_64" if defect in {"platform", "renamed-platform"} else platform
    files = {
        f"bin/{executable}": assets.ArchiveFile(b"exact candidate", mode=0o755),
        "bin/_internal/runtime.dat": b"runtime dependency",
        "providers.toml": b"version = 1\n",
    }
    name = assets.archive_name(selected_version, selected_platform)
    archive = assets.archive_bytes(files, selected_version, selected_platform)
    manifest = assets.asset_manifest(
        version=selected_version,
        platform=selected_platform,
        archive_name=name,
        archive=archive,
        files=files,
    )
    inventory = (
        {assets.archive_name(version, platform): archive, assets.manifest_name(platform): manifest}
        if defect in {"renamed-version", "renamed-platform"}
        else {name: archive, assets.manifest_name(selected_platform): manifest}
    )
    for filename, content in {
        **inventory,
        assets.CHECKSUM_NAME: assets.checksums(inventory),
    }.items():
        (source / filename).write_bytes(content)
    if defect == "archive":
        (source / name).write_bytes(archive + b"changed")
    elif defect == "output":
        output.mkdir()
        (output / "preserved").write_bytes(b"operator content")
    before = {p.name: p.read_bytes() for p in source.iterdir()}

    if defect:
        with pytest.raises((assets.AssetError, SystemExit)):
            commands.main(("materialize", "--source", str(source), "--output", str(output)))
        if defect == "output":
            assert (output / "preserved").read_bytes() == b"operator content"
        else:
            assert not output.exists()
    else:
        commands.main(("materialize", "--source", str(source), "--output", str(output)))
        assert (output / "bin" / executable).read_bytes() == b"exact candidate"
        assert (output / "bin/_internal/runtime.dat").read_bytes() == b"runtime dependency"
        assert (output / "providers.toml").read_bytes() == files["providers.toml"]
        if os.name != "nt":
            assert (output / "bin" / executable).stat().st_mode & 0o777 == 0o755
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


@pytest.mark.parametrize("defect", ["missing", "empty", "directory", "symlink"])
def test_materialization_refuses_invalid_asset_roots_before_output(tmp_path, defect):
    source = tmp_path / "assets"
    output = tmp_path / "runtime"
    if defect != "missing":
        source.mkdir()
    if defect == "directory":
        (source / "nested").mkdir()
    elif defect == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"unowned input")
        (source / "linked").symlink_to(target)
    with pytest.raises(SystemExit, match=r"unavailable|invalid"):
        commands.main(("materialize", "--source", str(source), "--output", str(output)))
    assert not output.exists()
