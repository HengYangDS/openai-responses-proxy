"""Exact source generation, capability and artifact admission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.deployment import strategy
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import idle_runtime


class TestControllerHandoffWiring(HandoffFixture):
    def test_capture_source_listener_requires_one_live_exact_product_generation(
        self, subtests, mocker
    ):
        ctx = install_context(Path(self.tempdir.name))
        runtime = idle_runtime()
        admitted = process.OwnedProcess(999, ctx.executable, 0.5)
        capture = mocker.patch.object(process, "capture_executable", return_value=admitted)
        alive = mocker.patch.object(process, "owned_process_alive", return_value=True)

        assert handoff.capture_source_listener(ctx, runtime) == admitted
        capture.assert_called_once_with(
            999,
            ctx.executable,
            roles={serving_roles.LISTENER_MODE, serving_roles.HANDOFF_CHILD_MODE},
        )
        alive.assert_called_once_with(admitted)

        for pid, generation, generation_alive in (
            (True, admitted, True),
            (0, admitted, True),
            (999, None, True),
            (999, admitted, False),
        ):
            with subtests.test(
                pid=pid,
                generation=generation,
                generation_alive=generation_alive,
            ):
                runtime["pid"] = pid
                capture.return_value = generation
                alive.return_value = generation_alive
                with pytest.raises(errors.InstallError, match="not verified"):
                    handoff.capture_source_listener(ctx, runtime)

    def test_capture_source_listener_does_not_depend_on_platform_tcp_attribution(self, mocker):
        ctx = install_context(Path(self.tempdir.name))
        runtime = idle_runtime()
        admitted = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "capture_executable", return_value=admitted)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        tcp_attribution = mocker.patch.object(process, "listener_pids", return_value=[])
        stale_identity_read = mocker.patch.object(
            process, "verified_proxy_listener_pids", return_value=[]
        )

        assert handoff.capture_source_listener(ctx, runtime) == admitted
        tcp_attribution.assert_not_called()
        stale_identity_read.assert_not_called()

    def test_runtime_supports_handoff_requires_a_complete_available_identity(self, subtests):
        incomplete = idle_runtime()
        incomplete.pop("serving_payload_sha256")
        cases: list[tuple[dict[str, object] | None, bool]] = [
            (idle_runtime(handoff_capabilities=["selected-generation-handoff"]), True),
            (
                idle_runtime(
                    handoff_state="finalized",
                    handoff_transaction_id="txn-previous-finalized",
                    handoff_capabilities=["selected-generation-handoff"],
                ),
                True,
            ),
        ]
        unsupported: list[dict[str, object] | None] = [
            {"handoff_protocol_version": 1},
            {"handoff_protocol_version": 2},
            incomplete,
            idle_runtime(accepting=False),
            idle_runtime(draining=True),
            idle_runtime(handoff_state="ready"),
            idle_runtime(handoff_capabilities=["repeatable"]),
            {},
            None,
            {"release": "1.0.24"},
            idle_runtime(
                handoff_state="finalized",
                handoff_transaction_id="txn-legacy-finalized",
                handoff_capabilities=None,
            ),
        ]
        cases.extend((runtime, False) for runtime in unsupported)
        for runtime, supported in cases:
            with subtests.test(runtime=runtime, supported=supported):
                assert strategy.runtime_supports_handoff(runtime) is supported

    def test_deployment_strategy_requires_complete_identity_and_explicit_repeatability(
        self, subtests
    ) -> None:
        finalized = idle_runtime(
            handoff_state="finalized",
            handoff_transaction_id="txn-previous-finalized",
        )
        cases = (
            (
                idle_runtime(
                    handoff_capabilities=[
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                        handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
                    ]
                ),
                "handoff",
            ),
            (
                {
                    **finalized,
                    "handoff_capabilities": [
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                        handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
                    ],
                },
                "handoff",
            ),
            (
                idle_runtime(
                    handoff_capabilities=[
                        handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY
                    ]
                ),
                "native_generation",
            ),
            (
                {**finalized, "handoff_capabilities": ["repeatable"]},
                "native_generation",
            ),
            ({**finalized, "handoff_capabilities": None}, "native_generation"),
            ({**finalized, "serving_payload_sha256": None}, "unsupported"),
            ({**finalized, "accepting": False}, "unsupported"),
        )

        for runtime, expected in cases:
            with subtests.test(runtime=runtime):
                assert strategy.deployment_strategy(runtime) == expected

    def test_expected_metadata_reads_and_validates_each_release_identity(self, *, mocker):
        root = Path(self.tempdir.name)
        manifest_path = root / inventory.MANIFEST_FILENAME

        with pytest.raises(errors.InstallError, match="payload files are unavailable") as raised:
            handoff.expected_metadata(str(root))
        assert str(root) not in str(raised.value)

        for payload in (
            b"not-json",
            b"{}",
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "bad",
                    "release_receipt_sha256": "f" * 64,
                }
            ).encode(),
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "BAD",
                }
            ).encode(),
            json.dumps(
                {
                    "release": "",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "f" * 64,
                }
            ).encode(),
        ):
            manifest_path.write_bytes(payload)
            with pytest.raises(errors.InstallError):
                handoff.expected_metadata(str(root))

        manifest_path.write_text(
            json.dumps(
                {
                    "release": "1.0.25",
                    "serving_payload_sha256": "a" * 64,
                    "release_receipt_sha256": "f" * 64,
                }
            ),
            encoding="utf-8",
        )
        (root / "VERSION").write_text("0.0.1\n", encoding="utf-8")
        mocker.patch.object(handoff.uuid, "uuid4", return_value=mocker.Mock(hex="txn-fixed"))
        metadata = handoff.expected_metadata(str(root))
        assert metadata["transaction_id"] == "txn-fixed"
        assert metadata["release"] == "1.0.25"
        assert metadata["manifest_sha256"] == handoff.digest.sha256_file(manifest_path)

    def test_digest_helpers_reject_noncanonical_values_and_hash_large_files(self, subtests):
        for value in (None, "a" * 63, "g" * 64, "A" * 64):
            with subtests.test(value=value):
                assert not handoff.digest.is_sha256(value)
        assert handoff.digest.is_sha256("a" * 64)

        path = Path(self.tempdir.name) / "large.bin"
        path.write_bytes(b"a" * (1024 * 1024 + 1))
        assert len(handoff.digest.sha256_file(path)) == 64
