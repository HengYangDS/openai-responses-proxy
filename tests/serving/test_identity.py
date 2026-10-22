"""Committed and loaded service-payload identity contracts."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import executable_relative
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload


class TestServiceIdentity:
    """Bind live service identity to one verified installed projection."""

    def test_runtime_payload_match_uses_one_canonical_field_mapping(self, subtests) -> None:
        expected = {
            "release": "1.2.3",
            "serving_payload_sha256": "a" * 64,
            "release_receipt_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        runtime = {
            "release": "1.2.3",
            "serving_payload_sha256": "a" * 64,
            "release_receipt_sha256": "b" * 64,
            "payload_manifest_sha256": "c" * 64,
        }

        assert identity.runtime_payload_matches(runtime, expected)
        for field in runtime:
            with subtests.test(field=field):
                assert not identity.runtime_payload_matches(
                    {**runtime, field: "different"}, expected
                )

    def test_committed_identity_rejects_runtime_and_manifest_drift(
        self, subtests, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, "1.2.3", mocker=mocker)

        committed = identity.committed_payload(Path(ctx.executable))

        assert committed is not None
        assert committed.release == "1.2.3"
        assert (
            committed.handoff()["manifest_sha256"]
            == hashlib.sha256(Path(projection.payload_manifest_path(ctx)).read_bytes()).hexdigest()
        )

        executable = Path(ctx.executable)
        original = executable.read_bytes()
        executable.write_bytes(original + b"tampered")
        assert identity.committed_payload(executable) is None

        executable.write_bytes(original)
        manifest_path = Path(projection.payload_manifest_path(ctx))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutations = (
            lambda value: value.__setitem__("schema_version", 1),
            lambda value: value.__setitem__("files", []),
            lambda value: value["files"].pop(executable_relative()),
            lambda value: value["serving_files"].pop(inventory.PROVIDER_MANIFEST),
            lambda value: value["files"].__setitem__(executable_relative(), "0" * 64),
            lambda value: value["serving_files"].__setitem__(inventory.PROVIDER_MANIFEST, "0" * 64),
            lambda value: value.__setitem__("release_receipt_sha256", "invalid"),
        )
        for mutate in mutations:
            with subtests.test(mutate=mutate):
                broken = json.loads(json.dumps(manifest))
                mutate(broken)
                manifest_path.write_text(json.dumps(broken), encoding="utf-8")
                assert identity.committed_payload(executable) is None
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert (
            identity.committed_payload(Path(ctx.install_dir, inventory.PROVIDER_MANIFEST)) is None
        )

    def test_loaded_identity_is_immutable_after_disk_drift(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        loaded = identity.freeze_loaded_payload(Path(ctx.executable))
        assert loaded is not None

        Path(ctx.executable).write_bytes(b"tampered-after-freeze")
        manifest = json.loads(Path(projection.payload_manifest_path(ctx)).read_text())

        assert loaded.release == "1.2.3"
        assert loaded.serving_payload_sha256 == manifest["serving_payload_sha256"]
        assert loaded.release_receipt_sha256 == manifest["release_receipt_sha256"]

    def test_selected_payload_executable_follows_one_verified_selector(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        transaction = install_payload(ctx, mocker=mocker)
        candidate = transaction.context

        assert identity.selected_payload_executable(Path(candidate.executable)) == Path(
            candidate.executable
        ).resolve(strict=True)

    @pytest.mark.parametrize(
        ("mutation", "message"),
        [
            ("loaded-payload", "loaded payload identity is unavailable"),
            ("missing-selector", "selector is invalid"),
            ("selector-symlink", "selector is invalid"),
            ("selector-schema", "selector is invalid"),
            ("selector-noncanonical", "selector is invalid"),
            ("selector-extra-field", "selector is invalid"),
            ("active-generation", "generation identity is invalid"),
            ("missing-executable", "selected payload executable is unavailable"),
            ("invalid-generation", "selected payload identity is invalid"),
        ],
    )
    def test_selected_payload_executable_rejects_unproved_authority(
        self, mutation: str, message: str, tmp_path: Path, *, mocker
    ) -> None:
        """Selector resolution fails closed at every authority boundary."""
        ctx = install_context(tmp_path)
        transaction = install_payload(ctx, mocker=mocker)
        executable = Path(transaction.context.executable)
        selector = generation.selector_path(ctx)

        if mutation == "loaded-payload":
            executable.write_bytes(b"corrupt")
        elif mutation == "missing-selector":
            selector.unlink()
        elif mutation == "selector-symlink":
            selector.unlink()
            target = tmp_path / "selector-target"
            target.write_bytes(b"{}")
            selector.symlink_to(target)
        elif mutation == "selector-schema":
            selector.write_bytes(
                digest.canonical_json(
                    {"schema_version": 2, "active": "a" * 32, "predecessor": None}
                )
            )
        elif mutation == "selector-noncanonical":
            selector.write_text(
                json.dumps(
                    {"schema_version": 1, "active": "a" * 32, "predecessor": None},
                    indent=2,
                ),
                encoding="utf-8",
            )
        elif mutation == "selector-extra-field":
            selector.write_bytes(
                digest.canonical_json(
                    {
                        "schema_version": 1,
                        "active": "a" * 32,
                        "predecessor": None,
                        "parallel_authority": True,
                    }
                )
            )
        elif mutation == "active-generation":
            selector.write_bytes(
                digest.canonical_json(
                    {"schema_version": 1, "active": "invalid", "predecessor": None}
                )
            )
        elif mutation == "missing-executable":
            selector.write_bytes(
                digest.canonical_json(
                    {"schema_version": 1, "active": "a" * 32, "predecessor": None}
                )
            )
        else:
            mocker.patch.object(identity, "committed_payload", return_value=None)

        with pytest.raises(ValueError, match=message):
            identity.selected_payload_executable(executable)

    def test_loaded_identity_rejects_a_manifest_for_the_other_platform(
        self, *, mocker, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ctx = install_context(tmp_path_factory.mktemp("case"))
        install_payload(ctx, mocker=mocker)
        root = Path(ctx.payload_dir)
        manifest_path = Path(projection.payload_manifest_path(ctx))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        windows_executable = root / inventory.WINDOWS_EXECUTABLE
        windows_executable.write_bytes(Path(ctx.executable).read_bytes())
        files = {
            inventory.WINDOWS_EXECUTABLE: hashlib.sha256(
                windows_executable.read_bytes()
            ).hexdigest(),
            inventory.PROVIDER_MANIFEST: manifest["serving_files"][inventory.PROVIDER_MANIFEST],
        }
        manifest["serving_files"] = files
        manifest["serving_payload_sha256"] = inventory.validated_serving_payload_sha256(files)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        assert identity.freeze_loaded_payload(Path(ctx.executable)) is None


def test_loaded_identity_accepts_only_exact_native_projection(subtests) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        executable = root / inventory.EXECUTABLE
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"native executable\n")
        provider_manifest = root / inventory.PROVIDER_MANIFEST
        provider_manifest.write_text(
            'version = 1\n[providers.fixture]\nbase_url = "https://fixture.invalid/v1"\n',
            encoding="utf-8",
        )
        files = {
            relative: digest.sha256_file(root / relative)
            for relative in inventory.required_runtime_files()
        }
        manifest = {
            "release": "1.2.3",
            "release_receipt_sha256": "a" * 64,
            "serving_files": files,
            "serving_payload_sha256": inventory.validated_serving_payload_sha256(files),
        }
        _write_manifest(root, manifest)
        frozen = identity.freeze_loaded_payload(executable)
        assert frozen is not None
        wrong_executable = root / "other"
        wrong_executable.write_bytes(b"other")
        cases = (
            ("wrong executable", None, wrong_executable),
            ("non-object files", {"serving_files": []}, executable),
            ("non-string digest", {"serving_files": {inventory.EXECUTABLE: 1}}, executable),
            (
                "incomplete files",
                {"serving_files": {inventory.EXECUTABLE: files[inventory.EXECUTABLE]}},
                executable,
            ),
            (
                "wrong digest",
                {"serving_files": {**files, inventory.EXECUTABLE: "0" * 64}},
                executable,
            ),
            ("wrong aggregate", {"serving_payload_sha256": "0" * 64}, executable),
            ("bad receipt", {"release_receipt_sha256": "bad"}, executable),
            ("missing release", {"release": ""}, executable),
        )
        for name, changes, candidate in cases:
            with subtests.test(name=name):
                if changes is not None:
                    _write_manifest(root, {**manifest, **changes})
                assert identity.freeze_loaded_payload(candidate) is None
                _write_manifest(root, manifest)
        _write_manifest(root, [])
        assert identity.freeze_loaded_payload(executable) is None
        _write_manifest(root, manifest)
        assert identity.freeze_loaded_payload(executable) is not None


def _write_manifest(root: Path, manifest: object) -> None:
    (root / inventory.MANIFEST_FILENAME).write_text(json.dumps(manifest))
