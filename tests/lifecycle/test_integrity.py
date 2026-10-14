"""Payload admission, manifest, and rollback validation contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import candidate as payload_candidate
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.lifecycle import projection as payload_projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import executable_relative
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import runtime_files

ROOT = Path(__file__).resolve().parents[2]


class TestPayloadValidation:
    """Payload admission, manifest, and rollback validation contracts."""

    def test_begin_rejects_existing_transaction_and_invalid_candidate_boundaries(
        self, subtests, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        transaction_root = Path(records.transaction_root(ctx))
        transaction_root.mkdir(parents=True)
        with pytest.raises(errors.RecoveryStateError, match="evidence is invalid"):
            begin_transaction(ctx, released_artifact(), mocker=mocker)

        valid = released_artifact()
        blobs, receipt = valid.peek_blobs(), dict(valid.receipt)
        cases = [
            (blobs, "1.2.3-rc1", "0" * 64, receipt, "version is invalid"),
            (blobs, "1.2.3", "short", receipt, "receipt digest is invalid"),
            (
                blobs,
                "1.2.3",
                "0" * 64,
                {**receipt, "version": "0.0.0"},
                "version mismatch",
            ),
            (blobs[:-1], "1.2.3", "0" * 64, receipt, "file set mismatch"),
            (
                blobs,
                "1.2.3",
                "0" * 64,
                {**receipt, "serving_files": []},
                "serving file set mismatch",
            ),
            (
                blobs,
                "1.2.3",
                "0" * 64,
                {**receipt, "serving_payload_sha256": "0" * 64},
                "serving identity mismatch",
            ),
        ]
        blob = blobs[0]

        invalid_mode = replace(blob)
        object.__setattr__(invalid_mode, "mode", "100600")

        cases += (
            (
                (invalid_mode, *blobs[1:]),
                "1.2.3",
                "0" * 64,
                receipt,
                "mode is invalid",
            ),
            (
                (replace(blob, sha256="0" * 64), *blobs[1:]),
                "1.2.3",
                "0" * 64,
                receipt,
                "digest mismatch",
            ),
        )
        for candidate_blobs, version, digest, candidate_receipt, message in cases:
            with (
                subtests.test(message=message),
                pytest.raises(errors.InstallError, match=message),
            ):
                payload_candidate.validate(candidate_blobs, version, digest, candidate_receipt)

    def test_digest_and_unowned_collision_boundaries_fail_closed(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        valid = {
            relative: hashlib.sha256(relative.encode()).hexdigest() for relative in runtime_files()
        }
        with pytest.raises(errors.InstallError, match="declared inventory"):
            payload_projection.manifest_serving_payload_sha256({})
        invalid = dict(valid)
        invalid[inventory.PROVIDER_MANIFEST] = "invalid"
        with pytest.raises(errors.InstallError, match="invalid serving payload"):
            payload_projection.manifest_serving_payload_sha256(invalid)

        ctx = install_context(tmp_path_factory.mktemp("case"))
        transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
        collision = Path(transaction.context.payload_dir, inventory.PROVIDER_MANIFEST)
        collision.parent.mkdir(parents=True, exist_ok=True)
        collision.write_text("unowned", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="candidate unowned collision"):
            transaction.commit_projection()

    def test_bundle_payload_accepts_manifested_nested_runtime_files(self) -> None:
        candidate = released_artifact()
        blobs = candidate.peek_blobs()
        nested_content = b"frozen-runtime"
        nested = artifact.ArtifactFile(
            path="bin/_internal/runtime.dat",
            mode="100644",
            blob_oid=hashlib.sha256(nested_content).hexdigest(),
            sha256=hashlib.sha256(nested_content).hexdigest(),
            content=nested_content,
        )
        expanded = (*blobs, nested)
        receipt = dict(candidate.receipt)
        receipt["serving_files"] = [item.path for item in expanded]
        receipt["serving_payload_sha256"] = payload_projection.manifest_serving_payload_sha256(
            {item.path: item.sha256 for item in expanded}
        )

        payload_candidate.validate(expanded, "1.2.3", "0" * 64, receipt)

    def test_bundle_prewarm_materializes_runs_and_rejects_failures(
        self, tmp_path: Path, subtests, *, mocker
    ) -> None:
        completed = mocker.patch.object(
            subprocess,
            "run",
            return_value=subprocess.CompletedProcess(("proxy", "--internal-prewarm"), 0),
        )
        ctx = install_context(tmp_path)
        executable = Path(ctx.executable)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"native")
        payload_candidate.prewarm(ctx)
        arguments = completed.call_args.args[0]
        assert arguments[0] == str(executable)
        assert arguments[1:] == [payload_candidate.serving_roles.PREWARM_MODE]
        environment = completed.call_args.kwargs["env"]
        assert environment[product_identity.environment_name("HOME")] == str(ctx.payload_dir)
        assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        assert "PYTHONHOME" not in environment
        assert "PYTHONPATH" not in environment
        assert completed.call_args.kwargs["timeout"] == 120

        for effect in (
            subprocess.CompletedProcess(("proxy", "--internal-prewarm"), 2),
            OSError("cannot execute"),
            subprocess.TimeoutExpired(("proxy", "--internal-prewarm"), 120),
        ):
            with subtests.test(effect=type(effect).__name__):
                mocker.patch.object(
                    subprocess,
                    "run",
                    side_effect=effect if isinstance(effect, BaseException) else None,
                    return_value=effect if not isinstance(effect, BaseException) else None,
                )
                with pytest.raises(errors.InstallError, match="prewarm failed"):
                    payload_candidate.prewarm(ctx)

    def test_transaction_filesystem_failures_remain_fail_closed(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
        mocker.patch.object(payload_digest, "sha256_file", return_value="0" * 64)
        with pytest.raises(errors.InstallError, match="installed payload digest mismatch"):
            transaction.commit_projection()

    def test_manifest_verifier_reports_each_metadata_boundary(
        self, subtests, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        def installed() -> tuple[runtime_context.RuntimeContext, Path, dict[str, object]]:
            ctx = install_context(tmp_path_factory.mktemp("case"))
            transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
            transaction.commit_projection()
            transaction.activate()
            transaction.finalize({"pid": 1})
            path = Path(payload_projection.payload_manifest_path(transaction.context))
            decoded: object = json.loads(path.read_text())
            assert isinstance(decoded, dict)
            assert all(isinstance(key, str) for key in decoded)
            manifest: dict[str, object] = {}
            for key, value in decoded.items():
                assert isinstance(key, str)
                manifest[key] = value
            return transaction.context, path, manifest

        ctx = install_context(tmp_path_factory.mktemp("case"))
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert detail == "installed payload manifest is unavailable"

        ctx, path, _ = installed()
        path.write_text("not-json")
        assert payload_projection.verify_payload_manifest(ctx)[1] == (
            "installed payload manifest is unavailable"
        )

        mutations = [
            (
                lambda value: value.update(schema_version=99),
                "manifest schema is unsupported",
            ),
            (lambda value: value.pop("release"), "manifest is incomplete"),
            (
                lambda value: value["files"].pop(executable_relative()),
                "manifest file set mismatch",
            ),
            (
                lambda value: value["serving_files"].pop(runtime_files()[-1]),
                "manifest serving file set mismatch",
            ),
            (
                lambda value: value["files"].update({executable_relative(): "short"}),
                "invalid digest",
            ),
            (
                lambda value: value["serving_files"].update(
                    {inventory.PROVIDER_MANIFEST: "0" * 64}
                ),
                "serving digest mismatch",
            ),
            (
                lambda value: value.update(release_receipt_sha256="short"),
                "receipt digest is invalid",
            ),
            (
                lambda value: value.update(release_receipt_sha256="0" * 64),
                "receipt digest mismatch",
            ),
        ]
        for mutate, message in mutations:
            ctx, path, manifest = installed()
            mutate(manifest)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with subtests.test(message=message):
                ok, detail = payload_projection.verify_payload_manifest(ctx)
                assert not ok
                assert message in detail

        ctx, _, _ = installed()
        Path(ctx.executable).unlink()
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert detail == "installed payload file is unavailable: bin/openai-responses-proxy"

        ctx, _, _ = installed()
        invalid_aggregate = mocker.patch.object(
            payload_projection,
            "manifest_serving_payload_sha256",
            side_effect=errors.InstallError("invalid serving payload digest"),
        )
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert "invalid serving payload" in detail
        mocker.stop(invalid_aggregate)

        ctx, _, _ = installed()
        unavailable_digest = mocker.patch.object(
            payload_digest,
            "sha256_file",
            side_effect=OSError("blocked"),
        )
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert detail == "installed payload file is unavailable: bin/openai-responses-proxy"
        mocker.stop(unavailable_digest)

        ctx, _, _ = installed()
        receipt = Path(ctx.payload_dir, inventory.RELEASE_RECEIPT_FILENAME)
        receipt.unlink()
        ok, detail = payload_projection.verify_payload_manifest(ctx)
        assert not ok
        assert detail == "installed release receipt is unavailable"

    def test_canonical_state_validation_fail_closed(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        path = Path(ctx.install_dir, "state.json")
        path.parent.mkdir(parents=True)
        path.write_text('{"value": 1}', encoding="utf-8")
        with pytest.raises(errors.InstallError, match="not canonical JSON"):
            owned_files.read_canonical_json(path, "state")
        path.write_bytes(b"\xff")
        with pytest.raises(errors.InstallError, match="unavailable or invalid"):
            owned_files.read_canonical_json(path, "state")

        installed_state = Path(records.installed_path(ctx))
        installed_state.write_bytes(payload_digest.canonical_json({"schema_version": 99}))
        with pytest.raises(errors.InstallError, match="schema is unsupported"):
            records.read_installed(ctx)
        with pytest.raises(errors.InstallError, match="state version is invalid"):
            records.require_version({"version": "latest"})
        with pytest.raises(errors.InstallError, match="state command path is invalid"):
            records.require_command({"command": "relative/command"})

    def test_owned_file_boundaries_reject_invalid_types_and_path_races(
        self, tmp_path: Path, *, mocker
    ) -> None:
        """Exercise each fail-closed filesystem type boundary without private host paths."""
        with pytest.raises(errors.InstallError, match="non-empty string"):
            owned_files.canonical_relative(None, "fixture")

        root_file = tmp_path / "root-file"
        root_file.write_text("not a directory", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="root is not a real directory"):
            owned_files.regular_file(root_file, "payload", "fixture")

        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(errors.InstallError, match="not canonical"):
            owned_files.canonical_relative("../payload", "fixture")
        ancestor = root / "ancestor"
        ancestor.write_text("not a directory", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="ancestor is not a directory"):
            owned_files.regular_file(root, "ancestor/payload", "fixture")

        linked_ancestor = root / "linked-ancestor"
        linked_ancestor.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(errors.InstallError, match="symlink ancestor"):
            owned_files.regular_file(root, "linked-ancestor/payload", "fixture")

        unavailable = mocker.patch.object(Path, "lstat", side_effect=PermissionError("blocked"))
        with pytest.raises(errors.InstallError, match="path is unavailable"):
            owned_files.regular_file(root, "payload", "fixture")
        mocker.stop(unavailable)

        symlink = root / "symlink"
        symlink.symlink_to(ancestor)
        with pytest.raises(errors.InstallError, match="path is a symlink"):
            owned_files.regular_file(root, "symlink", "fixture")

        directory = root / "directory"
        directory.mkdir()
        with pytest.raises(errors.InstallError, match="not a regular file"):
            owned_files.regular_file(root, "directory", "fixture")
        with pytest.raises(errors.InstallError, match="owned path is not a regular file"):
            owned_files.write_bytes(directory, b"content", root=root)

        target = root / "changed-type"
        calls = 0

        def change_target_type(_target: Path, _root: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                target.mkdir()

        mocker.patch.object(owned_files, "_real_parent", side_effect=change_target_type)
        with pytest.raises(errors.InstallError, match="owned path changed type"):
            owned_files.write_bytes(target, b"content", root=root)
        mocker.stopall()

        sequence = root / "sequence.json"
        sequence.write_text("[]", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="not a JSON object"):
            owned_files.read_json_object(sequence, "fixture JSON")
        sequence.write_bytes(b"\xff")
        with pytest.raises(errors.InstallError, match="unavailable or invalid"):
            owned_files.read_json_object(sequence, "fixture JSON")

        outside = tmp_path / "outside" / "payload"
        with pytest.raises(errors.InstallError, match="escapes its root"):
            owned_files._real_parent(outside, root)

        real_root = tmp_path / "real-root"
        real_root.mkdir()
        linked_root = tmp_path / "linked-root"
        linked_root.symlink_to(real_root, target_is_directory=True)
        with pytest.raises(errors.InstallError, match="root is a symlink"):
            owned_files._real_parent(linked_root / "payload", linked_root)

        parent_file = real_root / "parent"
        parent_file.write_text("not a directory", encoding="utf-8")
        with pytest.raises(errors.InstallError, match="ancestor is not a directory"):
            owned_files._real_parent(parent_file / "payload", real_root)

        root_type = mocker.patch.object(Path, "mkdir")
        directory_type = mocker.patch.object(Path, "is_dir", return_value=False)
        with pytest.raises(errors.InstallError, match="root is not a real directory"):
            owned_files._real_parent(real_root / "payload", real_root)
        mocker.stop(root_type)
        mocker.stop(directory_type)

        linked_parent = real_root / "linked-parent"
        linked_parent.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(errors.InstallError, match="symlink ancestor"):
            owned_files._real_parent(linked_parent / "payload", real_root)
