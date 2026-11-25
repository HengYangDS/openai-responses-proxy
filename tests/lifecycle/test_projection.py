"""Installed payload projection and purge behavior contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import runtime_files

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _write_payload_manifest(
    ctx: runtime_context.RuntimeContext, *, release_receipt_sha256: str | None = None
) -> Path:
    """Write the production manifest shape for a current test payload.

    Without a digest it binds the receipt beside the payload, creating one when absent.
    """
    install = Path(ctx.payload_dir)
    paths = sorted(inventory.REQUIRED_RUNTIME_FILES)
    digests = {relative: payload_digest.sha256_file(install / relative) for relative in paths}
    if release_receipt_sha256 is None:
        receipt = install / inventory.RELEASE_RECEIPT_FILENAME
        if not receipt.exists():
            receipt.write_bytes(b"{}\n")
        release_receipt_sha256 = payload_digest.sha256_file(receipt)
    manifest = payload_projection.manifest_for_digests("0.0.0", digests, release_receipt_sha256)
    path = payload_projection.payload_manifest_path(ctx)
    owned_files.write_bytes(path, payload_projection.manifest_bytes(manifest))
    return path


ROOT = Path(__file__).resolve().parents[2]


class TestPayloadProjection:
    """Manifest and installed-projection contracts over opaque release bytes."""

    def test_transaction_installs_complete_runtime_and_manifest(
        self, *, mocker: MockerFixture, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        manifest = json.loads(
            Path(payload_projection.payload_manifest_path(ctx)).read_text(encoding="utf-8")
        )
        assert sorted(manifest["files"]) == sorted(runtime_files())
        assert sorted(manifest["serving_files"]) == sorted(runtime_files())
        assert manifest[
            "serving_payload_sha256"
        ] == payload_projection.manifest_serving_payload_sha256(manifest["serving_files"])
        assert Path(ctx.executable).is_file()
        assert (Path(ctx.payload_dir) / inventory.PROVIDER_MANIFEST).is_file()

    def test_manifest_without_its_receipt_identity_is_incomplete(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        for blob in released_artifact().peek_blobs():
            target = Path(ctx.payload_dir, blob.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.content)
        path = _write_payload_manifest(ctx)
        assert payload_projection.verify_payload_manifest(ctx)[0]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["release_receipt_sha256"]
        owned_files.write_bytes(path, payload_projection.manifest_bytes(manifest))

        assert payload_projection.verify_payload_manifest(ctx) == (False, "manifest is incomplete")

    def test_manifest_detects_payload_and_aggregate_tampering(
        self, *, mocker: MockerFixture, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        proxy = Path(ctx.executable)
        proxy.write_bytes(proxy.read_bytes() + b"# tampered\n")
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert "hash mismatch" in detail

        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        manifest_path = Path(payload_projection.payload_manifest_path(ctx))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["serving_payload_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert detail == "serving payload aggregate mismatch"

    def test_purge_unlinks_only_manifest_owned_payload_and_preserves_unknown_content(
        self, *, mocker: MockerFixture, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        install = Path(ctx.payload_dir)
        unknown = Path(ctx.install_dir) / "operator-note.txt"
        unknown.write_text("keep\n", encoding="utf-8")

        files = payload_projection.owned_payload_files(ctx)
        remaining = payload_projection.purge_owned_files(install, files)

        assert remaining == ()
        assert unknown.read_text(encoding="utf-8") == "keep\n"
        assert not Path(payload_projection.payload_manifest_path(ctx)).exists()
        for relative in runtime_files():
            assert not (install / relative).exists()

    def test_purge_rejects_noncurrent_manifest_without_touching_unknown_content(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install = Path(ctx.payload_dir)
        claimed = {
            "VERSION": b"1.0.8\n",
            "operator-note.txt": b"keep\n",
        }
        for relative, content in claimed.items():
            target = install / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        manifest = {
            "schema_version": 1,
            "release": "1.0.8",
            "files": {
                relative: hashlib.sha256(content).hexdigest()
                for relative, content in claimed.items()
            },
        }
        (install / inventory.MANIFEST_FILENAME).write_bytes(payload_digest.canonical_json(manifest))

        with pytest.raises(errors.InstallError, match="manifest schema is unsupported"):
            payload_projection.owned_payload_files(ctx)
        assert (install / "operator-note.txt").read_bytes() == b"keep\n"

    def test_purge_fails_closed_without_one_valid_manifest(
        self, subtests: pytest.Subtests, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        for mutate, message in (
            (lambda _ctx: None, "manifest is required"),
            (
                lambda ctx: Path(payload_projection.payload_manifest_path(ctx)).symlink_to(
                    Path(ctx.payload_dir, "VERSION")
                ),
                "manifest is a symlink",
            ),
        ):
            with subtests.test(message=message):
                ctx = install_context(tmp_path_factory.mktemp("case"))
                marker = Path(ctx.payload_dir, "VERSION")
                marker.parent.mkdir(parents=True)
                marker.write_text("1.2.3\n", encoding="utf-8")
                mutate(ctx)
                with pytest.raises(errors.InstallError, match=message):
                    payload_projection.owned_payload_files(ctx)
                assert marker.exists()

    def test_serving_payload_identity_is_order_independent_and_length_delimited(
        self,
    ) -> None:
        digests = {
            relative: hashlib.sha256(relative.encode("utf-8")).hexdigest()
            for relative in runtime_files()
        }
        reverse_order = dict(reversed(tuple(digests.items())))
        assert payload_projection.manifest_serving_payload_sha256(
            digests
        ) == payload_projection.manifest_serving_payload_sha256(reverse_order)
        changed = dict(digests)
        changed[runtime_files()[-1]] = "0" * 64
        assert payload_projection.manifest_serving_payload_sha256(
            digests
        ) != payload_projection.manifest_serving_payload_sha256(changed)

    def test_owned_directory_and_empty_root_edges_fail_closed(
        self, *, mocker: MockerFixture, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        install = tmp_path_factory.mktemp("case")
        payload_projection.remove_empty_owned_directories(
            install, {"absent/payload", "present/payload"}
        )

        changed = install / "present"
        changed.write_text("not a directory", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="directory changed type"):
            payload_projection.remove_empty_owned_directories(install, {"present/payload"})

        changed.unlink()
        mocker.patch.object(Path, "rmdir", side_effect=OSError("busy"))
        with pytest.raises(errors.InstallError, match="root removal failed"):
            payload_projection._remaining_paths(install)

    def test_purge_preserves_native_failure_code_without_private_os_detail(
        self, tmp_path: Path, *, mocker: MockerFixture
    ) -> None:
        target = tmp_path / "owned.so"
        target.write_bytes(b"payload")
        failure = PermissionError(13, "private operating-system detail")
        mocker.patch.object(Path, "unlink", side_effect=failure)

        with pytest.raises(errors.InstallError) as stopped:
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        assert str(stopped.value) == "installed payload purge failed: owned.so (OS error 13)"
        assert stopped.value.__cause__ is failure
        assert target.read_bytes() == b"payload"

    def test_purge_does_not_retry_a_failed_deletion_or_change_permissions(
        self, tmp_path: Path, *, mocker: MockerFixture
    ) -> None:
        """A native denial keeps both owned and unknown content intact."""
        target = tmp_path / "owned.so"
        note = tmp_path / "operator-note.txt"
        target.write_bytes(b"payload")
        note.write_bytes(b"operator content")
        unlink = mocker.patch.object(Path, "unlink", side_effect=PermissionError(13, "denied"))
        chmod = mocker.spy(Path, "chmod")

        with pytest.raises(errors.InstallError, match="OS error 13"):
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        unlink.assert_called_once()
        chmod.assert_not_called()
        assert target.read_bytes() == b"payload"
        assert note.read_bytes() == b"operator content"

    @pytest.mark.parametrize("replacement", ["bytes", "identity", "ancestor", "linked-ancestor"])
    def test_purge_rechecks_captured_identity_after_the_read_boundary(
        self, tmp_path: Path, replacement: str, *, mocker: MockerFixture
    ) -> None:
        """Replacement content cannot inherit the captured file authority."""
        payload_root = tmp_path / "payload"
        directory = payload_root / "bin"
        directory.mkdir(parents=True)
        target = directory / "owned.so"
        target.write_bytes(b"payload")
        expected = payload_digest.sha256_file(target)
        original_read = payload_projection.owned_files.read_bytes

        def replace_before_read(path: Path, *, root: Path, label: str) -> bytes:
            if replacement == "bytes":
                path.write_bytes(b"replacement")
            elif replacement == "identity":
                path.rename(directory / "captured.so")
                path.write_bytes(b"payload")
            else:
                directory.rename(payload_root / "captured")
                if replacement == "linked-ancestor":
                    directory.symlink_to(payload_root / "captured", target_is_directory=True)
                else:
                    directory.mkdir()
                    path.hardlink_to(payload_root / "captured" / path.name)
            return original_read(path, root=root, label=label)

        mocker.patch.object(payload_projection.owned_files, "read_bytes", replace_before_read)
        unlink = mocker.spy(Path, "unlink")

        with pytest.raises(errors.InstallError, match=r"identity changed|symlink"):
            payload_projection.purge_owned_files(payload_root, {"bin/owned.so": expected})

        unlink.assert_not_called()
        assert target.read_bytes() == (b"replacement" if replacement == "bytes" else b"payload")

    def test_purge_and_residue_inventory_report_filesystem_failures(
        self, *, mocker: MockerFixture, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        files = payload_projection.owned_payload_files(ctx)
        mocker.patch.object(Path, "unlink", side_effect=OSError("blocked"))
        with pytest.raises(errors.InstallError, match="purge failed"):
            payload_projection.purge_owned_files(Path(ctx.payload_dir), files)
        mocker.stopall()

        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        files = payload_projection.owned_payload_files(ctx)
        residual = mocker.patch.object(Path, "exists", return_value=True)
        with pytest.raises(errors.InstallError, match="remains after purge"):
            payload_projection.purge_owned_files(Path(ctx.payload_dir), files)
        mocker.stop(residual)

        absent = tmp_path_factory.mktemp("case") / "absent"
        assert payload_projection._remaining_paths(absent) == ()
        linked = absent.with_name("linked")
        linked.symlink_to(absent.parent, target_is_directory=True)
        with pytest.raises(errors.InstallError, match="root is not a real directory"):
            payload_projection._remaining_paths(linked)

        root = tmp_path_factory.mktemp("case")
        mocker.patch.object(Path, "rglob", side_effect=OSError("blocked"))
        with pytest.raises(errors.InstallError, match="residue inventory failed"):
            payload_projection._remaining_paths(root)

        ctx = install_context(tmp_path_factory.mktemp("case"))
        for blob in released_artifact().peek_blobs():
            target = Path(ctx.payload_dir, blob.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.content)
        manifest = _write_payload_manifest(ctx, release_receipt_sha256="0" * 64)
        assert json.loads(manifest.read_text())["release_receipt_sha256"] == "0" * 64
