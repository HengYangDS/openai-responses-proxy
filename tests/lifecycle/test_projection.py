"""Installed payload projection and purge behavior contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import runtime_files

ROOT = Path(__file__).resolve().parents[2]


class TestPayloadProjection:
    """Manifest and installed-projection contracts over opaque release bytes."""

    def test_transaction_installs_complete_runtime_and_manifest(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
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

    def test_fixture_manifest_can_omit_receipt_identity(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        for blob in released_artifact().peek_blobs():
            target = Path(ctx.payload_dir, blob.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.content)
        path = payload_projection._write_payload_manifest_for_fixture(ctx)
        assert "release_receipt_sha256" not in json.loads(Path(path).read_text())
        assert payload_projection.verify_payload_manifest(ctx)[0]

    def test_manifest_detects_payload_and_aggregate_tampering(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
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
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
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
        self, subtests, tmp_path_factory: pytest.TempPathFactory
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
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
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

    @pytest.mark.parametrize("native_code", [None, 5, 32])
    def test_purge_preserves_native_failure_code_without_private_os_detail(
        self, tmp_path: Path, native_code: int | None, *, mocker
    ) -> None:
        target = tmp_path / "owned.pyd"
        target.write_bytes(b"payload")
        failure = PermissionError(13, "private operating-system detail")
        if native_code is not None:
            mocker.patch.object(failure, "winerror", native_code, create=True)
        mocker.patch.object(Path, "unlink", side_effect=failure)
        mocker.patch("time.monotonic", side_effect=[0.0, 5.0])

        with pytest.raises(errors.InstallError) as stopped:
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        assert str(stopped.value) == (
            f"installed payload purge failed: owned.pyd (OS error {native_code or 13})"
        )
        assert stopped.value.__cause__ is failure
        assert target.read_bytes() == b"payload"

    @pytest.mark.parametrize("native_code", [5, 32])
    def test_purge_rechecks_owned_bytes_after_a_transient_windows_delete_failure(
        self, tmp_path: Path, native_code: int, *, mocker
    ) -> None:
        """A released native lock permits disposal without changing permissions."""
        target = tmp_path / "owned.dll"
        target.write_bytes(b"payload")
        expected = payload_digest.sha256_file(target)
        failure = PermissionError(13, "private native detail")
        mocker.patch.object(failure, "winerror", native_code, create=True)
        original_unlink = Path.unlink
        attempts = []

        def unlink(path: Path, *args, **kwargs) -> None:
            if path == target:
                attempts.append(path)
                if len(attempts) == 1:
                    raise failure
            original_unlink(path, *args, **kwargs)

        mocker.patch.object(Path, "unlink", side_effect=unlink, autospec=True)
        sleep = mocker.patch("time.sleep")
        read = mocker.spy(payload_projection.owned_files, "read_bytes")
        chmod = mocker.spy(Path, "chmod")

        assert payload_projection.purge_owned_files(tmp_path, {target.name: expected}) == ()
        assert len(attempts) == 2
        assert read.call_count == 2
        sleep.assert_called_once()
        chmod.assert_not_called()

    @pytest.mark.parametrize("replacement", ["bytes", "identity", "ancestor", "linked-ancestor"])
    def test_windows_purge_retry_rejects_a_changed_file_or_ancestor(
        self, tmp_path: Path, replacement: str, *, mocker
    ) -> None:
        """A retry never inherits disposal authority for replacement content."""
        install = tmp_path / "install"
        directory = install / "bin"
        directory.mkdir(parents=True)
        target = directory / "owned.dll"
        target.write_bytes(b"payload")
        expected = payload_digest.sha256_file(target)
        failure = PermissionError(13, "private native detail")
        mocker.patch.object(failure, "winerror", 5, create=True)
        unlink = mocker.patch.object(Path, "unlink", side_effect=failure)

        def substitute(_seconds: float) -> None:
            if replacement == "bytes":
                target.write_bytes(b"replacement")
            elif replacement == "identity":
                target.rename(directory / "captured.dll")
                target.write_bytes(b"payload")
            else:
                directory.rename(install / "captured")
                if replacement == "linked-ancestor":
                    directory.symlink_to(install / "captured", target_is_directory=True)
                else:
                    directory.mkdir()
                    target.hardlink_to(install / "captured" / target.name)

        mocker.patch("time.sleep", side_effect=substitute)

        with pytest.raises(errors.InstallError, match=r"identity changed|symlink ancestor"):
            payload_projection.purge_owned_files(install, {"bin/owned.dll": expected})

        unlink.assert_called_once()
        assert target.read_bytes() == (b"replacement" if replacement == "bytes" else b"payload")

    @pytest.mark.parametrize("native_code", [5, 32])
    def test_windows_purge_retry_stops_at_the_disposal_deadline(
        self, tmp_path: Path, native_code: int, *, mocker
    ) -> None:
        """Persistent native denial keeps both owned and unknown content intact."""
        target = tmp_path / "owned.dll"
        note = tmp_path / "operator-note.txt"
        target.write_bytes(b"payload")
        note.write_bytes(b"operator content")
        failure = PermissionError(13, "private native detail")
        mocker.patch.object(failure, "winerror", native_code, create=True)
        unlink = mocker.patch.object(Path, "unlink", side_effect=failure)
        mocker.patch("time.monotonic", side_effect=[0.0, 0.0, 0.0, 5.0])
        sleep = mocker.patch("time.sleep")

        with pytest.raises(errors.InstallError) as raised:
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        assert raised.value.__cause__ is failure
        assert f"OS error {native_code}" in str(raised.value)
        assert unlink.call_count == 2
        sleep.assert_called_once()
        assert target.read_bytes() == b"payload"
        assert note.read_bytes() == b"operator content"

    @pytest.mark.parametrize("native_code", [None, 87])
    def test_nontransient_purge_failure_does_not_retry(
        self, tmp_path: Path, native_code: int | None, *, mocker
    ) -> None:
        """A POSIX denial or unrelated Windows error is not a sharing retry."""
        target = tmp_path / "owned.dll"
        target.write_bytes(b"payload")
        failure = PermissionError(13, "private native detail")
        if native_code is not None:
            mocker.patch.object(failure, "winerror", native_code, create=True)
        unlink = mocker.patch.object(Path, "unlink", side_effect=failure)
        sleep = mocker.patch("time.sleep")

        with pytest.raises(errors.InstallError):
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        unlink.assert_called_once()
        sleep.assert_not_called()

    def test_windows_purge_retry_cannot_renew_the_shared_deadline(
        self, tmp_path: Path, *, mocker
    ) -> None:
        """A second obstructed file shares the first file's bounded wait."""
        first = tmp_path / "first.dll"
        second = tmp_path / "second.dll"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        digests = {path.name: payload_digest.sha256_file(path) for path in (first, second)}
        failure = PermissionError(13, "private native detail")
        mocker.patch.object(failure, "winerror", 32, create=True)
        original_unlink = Path.unlink
        attempted = []

        def unlink(path: Path, *args, **kwargs) -> None:
            attempted.append(path.name)
            if attempted != [first.name, first.name]:
                raise failure
            original_unlink(path, *args, **kwargs)

        mocker.patch.object(Path, "unlink", side_effect=unlink, autospec=True)
        mocker.patch("time.monotonic", side_effect=[0.0, 0.0, 0.0, 5.0])
        sleep = mocker.patch("time.sleep")

        with pytest.raises(errors.InstallError, match=r"second\.dll.*OS error 32"):
            payload_projection.purge_owned_files(tmp_path, digests)

        assert attempted == [first.name, first.name, second.name]
        sleep.assert_called_once()
        assert not first.exists()
        assert second.read_bytes() == b"second"

    @pytest.mark.parametrize("replacement", ["identity", "ancestor"])
    def test_purge_rechecks_captured_identity_after_the_read_boundary(
        self, tmp_path: Path, replacement: str, *, mocker
    ) -> None:
        """Equal replacement bytes cannot inherit the captured file authority."""
        root = tmp_path / "payload"
        directory = root / "bin"
        directory.mkdir(parents=True)
        target = directory / "owned.dll"
        target.write_bytes(b"payload")
        expected = payload_digest.sha256_file(target)
        original_read = payload_projection.owned_files.read_bytes

        def replace_before_read(path: Path, **kwargs) -> bytes:
            if replacement == "identity":
                path.rename(directory / "captured.dll")
                path.write_bytes(b"payload")
            else:
                directory.rename(root / "captured")
                directory.mkdir()
                path.hardlink_to(root / "captured" / path.name)
            return original_read(path, **kwargs)

        mocker.patch.object(payload_projection.owned_files, "read_bytes", replace_before_read)
        unlink = mocker.spy(Path, "unlink")

        with pytest.raises(errors.InstallError, match="identity changed"):
            payload_projection.purge_owned_files(root, {"bin/owned.dll": expected})

        unlink.assert_not_called()
        assert target.read_bytes() == b"payload"

    @pytest.mark.parametrize("exhausted_at", ["retry-entry", "after-read"])
    def test_purge_does_not_unlink_after_the_sharing_deadline(
        self, tmp_path: Path, exhausted_at: str, *, mocker
    ) -> None:
        """Budget exhaustion refuses even a retry that could now succeed."""
        target = tmp_path / "owned.dll"
        target.write_bytes(b"payload")
        failure = PermissionError(13, "private native detail")
        mocker.patch.object(failure, "winerror", 32, create=True)
        original_unlink = Path.unlink
        attempts = []

        def unlink(path: Path, *args, **kwargs) -> None:
            attempts.append(path)
            if len(attempts) == 1:
                raise failure
            original_unlink(path, *args, **kwargs)

        mocker.patch.object(Path, "unlink", side_effect=unlink, autospec=True)
        times = [0.0, 5.0] if exhausted_at == "retry-entry" else [0.0, 0.0, 5.0]
        mocker.patch("time.monotonic", side_effect=times)
        mocker.patch("time.sleep")
        read = mocker.spy(payload_projection.owned_files, "read_bytes")

        with pytest.raises(errors.InstallError, match="OS error 32") as raised:
            payload_projection.purge_owned_files(
                tmp_path, {target.name: payload_digest.sha256_file(target)}
            )

        assert raised.value.__cause__ is failure
        assert attempts == [target]
        assert read.call_count == (1 if exhausted_at == "retry-entry" else 2)
        assert target.read_bytes() == b"payload"

    def test_purge_and_residue_inventory_report_filesystem_failures(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
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
        manifest = payload_projection._write_payload_manifest_for_fixture(
            ctx, release_receipt_sha256="0" * 64
        )
        assert json.loads(manifest.read_text())["release_receipt_sha256"] == "0" * 64
