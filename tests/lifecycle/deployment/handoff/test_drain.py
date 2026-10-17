"""Captured predecessor drain and admission recovery."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.supervision import process
from tests.lifecycle.deployment.handoff.fixtures import HandoffFixture
from tests.lifecycle.fixtures import install_context
from tests.serving.handoff.fixtures import Response
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import idle_runtime
from tests.serving.handoff.fixtures import matching_health


class TestControllerHandoffWiring(HandoffFixture):
    def test_native_replacement_drains_only_the_captured_predecessor(self, *, mocker) -> None:
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        runtime_reader = mocker.Mock(
            side_effect=(
                idle_runtime(),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=2,
                    active_handlers=5,
                ),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=0,
                    active_handlers=2,
                ),
                idle_runtime(
                    draining=True,
                    accepting=False,
                    active_responses=0,
                    active_handlers=1,
                ),
            )
        )
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True, "active_responses": 2}, status=200),
        )
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(process, "listener_pids", return_value=[999])

        handoff.drain_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=runtime_reader,
            timeout_seconds=1,
        )

        request = opened.call_args.args[0]
        assert request.full_url.endswith("/control/drain")
        assert request.method == "POST"
        assert request.headers["X-openai-responses-proxy-drain-lease-seconds"] == "2"
        assert runtime_reader.call_count == 4

    def test_failed_native_replacement_reopens_the_captured_predecessor(self, *, mocker) -> None:
        """A failed replacement must not leave the surviving listener drained."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": False}, status=200),
        )
        mocker.patch.object(process, "owned_process_alive", return_value=True)

        assert handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(
                side_effect=(
                    idle_runtime(draining=True, accepting=False),
                    idle_runtime(),
                )
            ),
        )

        request = opened.call_args.args[0]
        assert request.full_url.endswith("/control/drain")
        assert request.method == "DELETE"

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (Response({}, status=500), "HTTP 500"),
            (
                Response(b"x" * (handoff._MAX_BODY_BYTES + 1), status=200),
                "response is too large",
            ),
            (Response(b"[]", status=200), "did not reopen Responses admission"),
            (urllib.error.URLError("offline"), "unavailable"),
            (ValueError("bad timeout"), "unavailable"),
        ],
    )
    def test_failed_native_replacement_rejects_invalid_resume_control(
        self, response: object, message: str, *, mocker
    ) -> None:
        """Rollback admission must be acknowledged by the captured predecessor."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        opened = mocker.patch.object(handoff.loopback, "open_request")
        if isinstance(response, BaseException):
            opened.side_effect = response
        else:
            opened.return_value = response

        with pytest.raises(errors.InstallError, match=message):
            handoff.resume_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
            )

    def test_failed_native_replacement_requires_the_same_runtime_after_resume(
        self, *, mocker
    ) -> None:
        """A changed generation after DELETE cannot prove restored admission."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": False}, status=200),
        )

        assert not handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=mocker.Mock(side_effect=(idle_runtime(), None)),
        )

    def test_failed_native_replacement_does_not_reopen_a_replaced_listener(self, *, mocker) -> None:
        """A successor that already owns the port must not receive predecessor control."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=False)
        opened = mocker.patch.object(handoff.loopback, "open_request")

        assert not handoff.resume_responses(
            ctx,
            source_listener=source,
            source_runtime=idle_runtime(),
            runtime_reader=lambda _ctx: None,
        )

        opened.assert_not_called()

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (Response({}, status=500), "HTTP 500"),
            (
                Response(b"x" * (handoff._MAX_BODY_BYTES + 1), status=200),
                "response is too large",
            ),
            (Response(b"[]", status=200), "did not close Responses admission"),
            (urllib.error.URLError("offline"), "unavailable"),
            (ValueError("bad timeout"), "unavailable"),
        ],
    )
    def test_native_replacement_rejects_invalid_drain_control(
        self, response: object, message: str, *, mocker
    ) -> None:
        """Native replacement proceeds only after an exact drain acknowledgement."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        opened = mocker.patch.object(handoff.loopback, "open_request")
        if isinstance(response, BaseException):
            opened.side_effect = response
        else:
            opened.return_value = response

        with pytest.raises(errors.InstallError, match=message):
            handoff.drain_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: idle_runtime(),
                timeout_seconds=1,
            )

    def test_native_replacement_rejects_unproved_or_changed_predecessor(
        self, subtests, *, mocker
    ) -> None:
        """Drain remains bound to the captured process generation until completion."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        opened = mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True}, status=200),
        )

        with subtests.test(case="not-admitted"):
            mocker.patch.object(process, "owned_process_alive", return_value=False)
            with pytest.raises(errors.InstallError, match="no longer active"):
                handoff.drain_responses(
                    ctx,
                    source_listener=source,
                    source_runtime=idle_runtime(),
                    runtime_reader=lambda _ctx: None,
                    timeout_seconds=1,
                )
            opened.assert_not_called()

        with subtests.test(case="changed-during-drain"):
            mocker.patch.object(process, "owned_process_alive", return_value=True)
            runtime_reader = mocker.Mock(side_effect=(idle_runtime(), None))
            with pytest.raises(errors.InstallError, match="changed while draining"):
                handoff.drain_responses(
                    ctx,
                    source_listener=source,
                    source_runtime=idle_runtime(),
                    runtime_reader=runtime_reader,
                    timeout_seconds=1,
                )
            assert runtime_reader.call_count == 2

    def test_native_replacement_drain_timeout_is_bounded(self, *, mocker) -> None:
        """A predecessor that never reaches zero active Responses fails at the bound."""
        ctx = install_context(Path(self.tempdir.name))
        source = process.OwnedProcess(999, ctx.executable, 0.5)
        mocker.patch.object(process, "owned_process_alive", return_value=True)
        mocker.patch.object(
            handoff.loopback,
            "open_request",
            return_value=Response({"draining": True}, status=200),
        )
        mocker.patch.object(handoff.time, "monotonic", side_effect=[0.0, 0.0, 2.0])
        mocker.patch.object(handoff.time, "sleep")

        with pytest.raises(errors.InstallError, match="within 1s"):
            handoff.drain_responses(
                ctx,
                source_listener=source,
                source_runtime=idle_runtime(),
                runtime_reader=lambda _ctx: matching_health(
                    999,
                    {
                        **expected_metadata(),
                        "release": "1.0.24",
                        "release_receipt_sha256": "e" * 64,
                    },
                    draining=True,
                    accepting=False,
                    active_responses=1,
                ),
                timeout_seconds=1,
            )
