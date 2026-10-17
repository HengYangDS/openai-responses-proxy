"""Exact serving observation and controller-loss resolution."""

from __future__ import annotations

import urllib.error

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_request_handoff_resolves_finalized_rolled_back_and_unknown(
        self, subtests, *, mocker
    ) -> None:
        current = self.current_runtime()
        expected = FakeTransaction(self.ctx).expected
        successor = self.successor()
        source_listener = process.OwnedProcess(111, self.ctx.executable, 1.0)
        for response, resolution, expected_result in (
            ({"runtime": successor}, None, successor),
            ({"runtime": None}, ("finalized", successor), successor),
        ):
            with subtests.test(resolution=resolution):
                mocker.patch.object(apply.handoff, "request", return_value=response)
                mocker.patch.object(
                    apply.handoff,
                    "resolve_after_controller_failure",
                    return_value=resolution,
                )
                assert (
                    apply.request_handoff(
                        self.ctx,
                        expected,
                        current=current,
                        source_listener=source_listener,
                        runtime_reader=lambda _ctx: current,
                        timeout_seconds=1,
                    )
                    == expected_result
                )

        original = errors.InstallError("handoff failed")
        mocker.patch.object(apply.handoff, "request", side_effect=original)
        mocker.patch.object(
            apply.handoff,
            "resolve_after_controller_failure",
            return_value=("rolled_back", current),
        )
        with pytest.raises(errors.InstallError, match="handoff failed"):
            apply.request_handoff(
                self.ctx,
                expected,
                current=current,
                source_listener=source_listener,
                runtime_reader=lambda _ctx: current,
                timeout_seconds=1,
            )

        mocker.patch.object(
            apply.handoff,
            "resolve_after_controller_failure",
            return_value=("unknown", None),
        )
        with pytest.raises(apply.UnknownDeploymentOutcome):
            apply.request_handoff(
                self.ctx,
                expected,
                current=current,
                source_listener=source_listener,
                runtime_reader=lambda _ctx: current,
                timeout_seconds=1,
            )

    def test_wait_for_serving_runtime_requires_exact_identity(self, *, mocker) -> None:
        expected = FakeTransaction(self.ctx).expected
        match = self.successor()
        snapshots = [None, {**match, "pid": True}, {**match, "release": "wrong"}, match]
        mocker.patch.object(process, "verified_proxy_listener_pids", side_effect=[[], [222]])
        mocker.patch.object(apply.time, "monotonic", side_effect=map(float, range(6)))
        mocker.patch.object(apply.time, "sleep")
        assert (
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: snapshots.pop(0),
                timeout_seconds=10,
            )
            == match
        )

        mocker.patch.object(apply.time, "monotonic", side_effect=[0.0, 0.1, 0.2])
        with pytest.raises(errors.InstallError, match="SERVING identity"):
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: None,
                timeout_seconds=0.15,
            )

    def test_wait_for_serving_runtime_accepts_exact_process_when_tcp_inventory_lags(
        self, *, mocker
    ) -> None:
        """Loopback identity and exact process ownership outlive stale TCP inventory."""
        expected = FakeTransaction(self.ctx).expected
        match = self.successor()
        owned = process.OwnedProcess(222, self.ctx.executable, 1.0)
        mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[])
        mocker.patch.object(process, "capture_executable", return_value=owned)

        assert (
            apply.wait_for_serving_runtime(
                self.ctx,
                expected,
                runtime_reader=lambda _ctx: match,
                timeout_seconds=0.2,
            )
            == match
        )

    def test_read_runtime_accepts_only_http_200_json_objects(self, *, mocker) -> None:
        class Response:
            def __init__(self, status: int, payload: bytes) -> None:
                self.status, self.payload = status, payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        open_request = mocker.patch.object(
            control.loopback,
            "open_request",
            return_value=Response(200, b'{"pid": 222}'),
        )
        assert control.read_runtime(self.ctx) == {"pid": 222}
        for response in (
            Response(204, b""),
            Response(200, b"[]"),
            Response(200, b"bad"),
        ):
            open_request.return_value = response
            assert control.read_runtime(self.ctx) is None
        open_request.side_effect = urllib.error.URLError("offline")
        assert control.read_runtime(self.ctx) is None
