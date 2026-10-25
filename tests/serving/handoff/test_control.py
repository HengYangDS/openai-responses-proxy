"""Loopback handoff control handler contracts."""

from __future__ import annotations

import io
import json
import socket
import threading
import time
from pathlib import Path

from openai_responses_proxy.serving import control
from openai_responses_proxy.serving.handoff import protocol as handoff_protocol_module
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_handler
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import runtime_state_module

ROOT = Path(__file__).resolve().parents[3]


class TestHandoffControlHandler:
    def setup_method(self):
        self.bindings = control.Bindings(
            runtime_status=entrypoint_module.runtime_status,
            handoff_context=entrypoint_module._handoff_context,
        )

    def test_handler_transfers_ownership_before_acknowledging_ready(self, *, mocker):
        order = []
        handler = fake_handler(expected_metadata(), mocker=mocker)
        handler.wfile.write.side_effect = lambda chunk: order.append(("write", chunk))
        handler.wfile.flush.side_effect = lambda: order.append(("flush", None))
        expected = expected_metadata()
        prepared = {"child": mocker.Mock(runtime_pid=999), "expected": expected}
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        mocker.patch.object(handoff_module, "prepare", return_value=prepared)
        thread_cls = mocker.patch("threading.Thread")
        thread_cls.return_value.start.side_effect = lambda: order.append(("start", None))
        control.prepare_handoff(handler, self.bindings)
        assert [event for event, _ in order] == ["start", "write", "flush"]
        body = json.loads(order[1][1])
        assert (
            handler.send_response.call_args.args[0],
            body["child_pid"],
            body["transaction_id"],
        ) == (
            202,
            999,
            expected["transaction_id"],
        )
        assert thread_cls.call_args.kwargs["target"] == handoff_module.commit

    def test_handler_keeps_commit_after_ready_response_disconnect(self, *, mocker):
        handler = fake_handler(expected_metadata(), mocker=mocker)
        handler.wfile.write.side_effect = BrokenPipeError("controller disconnected")
        expected = expected_metadata()
        prepared = {"child": mocker.Mock(runtime_pid=999), "expected": expected}
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        mocker.patch.object(handoff_module, "prepare", return_value=prepared)
        thread_cls = mocker.patch("threading.Thread")

        control.prepare_handoff(handler, self.bindings)

        thread_cls.return_value.start.assert_called_once_with()
        assert thread_cls.call_args.kwargs["target"] == handoff_module.commit

    def test_handler_rejects_non_loopback_clients(self, *, mocker):
        handler = fake_handler(expected_metadata(), mocker=mocker)
        handler.client_address = ("example.invalid", 51234)
        prepare = mocker.patch.object(handoff_module, "prepare")
        control.prepare_handoff(handler, self.bindings)
        prepare.assert_not_called()
        assert handler.send_error.call_args.args[0] == 403

    def test_handler_returns_409_when_a_handoff_is_already_in_progress(self, *, mocker):
        fake_self = fake_handler(expected_metadata(), mocker=mocker)
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        mocker.patch.object(
            handoff_module,
            "prepare",
            side_effect=handoff_module.HandoffConflictError("a handoff is already in progress"),
        )
        control.prepare_handoff(fake_self, self.bindings)
        fake_self.send_response.assert_called_once_with(409)

    def test_drain_control_enforces_loopback_and_projects_the_lease(self, *, mocker):
        remote = fake_handler({}, mocker=mocker)
        remote.client_address = ("example.invalid", 51234)
        set_draining = mocker.patch.object(runtime_state_module, "set_draining")
        control.set_drain(remote, True)
        set_draining.assert_not_called()
        remote.send_error.assert_called_once_with(
            403, "drain control is available only to a local native controller"
        )

        local = fake_handler({}, mocker=mocker)
        local.headers["X-OpenAI-Responses-Proxy-Drain-Lease-Seconds"] = "41"
        set_draining = mocker.patch.object(
            runtime_state_module,
            "set_draining",
            return_value={"draining": True, "drain_generation": 1},
        )
        control.set_drain(local, True)
        set_draining.assert_called_once_with(True, lease_seconds="41")
        local.send_response.assert_called_once_with(200)

        reopened = fake_handler({}, mocker=mocker)
        set_draining = mocker.patch.object(
            runtime_state_module,
            "set_draining",
            return_value={"draining": False, "drain_generation": 2},
        )
        control.set_drain(reopened, False)
        set_draining.assert_called_once_with(False, lease_seconds=None)

    def test_status_control_serializes_the_bound_runtime_snapshot(self, *, mocker):
        fake_self = fake_handler({}, mocker=mocker)
        bindings = control.Bindings(
            runtime_status=lambda: {"ok": True, "pid": 42},
            handoff_context=entrypoint_module._handoff_context,
        )
        control.send_status(fake_self, bindings)
        fake_self.send_response.assert_called_once_with(200)
        assert json.loads(fake_self.wfile.write.call_args.args[0]) == {
            "ok": True,
            "pid": 42,
        }

    def test_handler_rejects_invalid_control_envelopes_before_prepare(self, subtests, *, mocker):
        cases = (
            ({"Content-Length": "not-an-int"}, b"{}", 400),
            ({"Content-Length": "0"}, b"", 413),
            (
                {"Content-Length": str(handoff_protocol_module.HANDOFF_CONTROL_MAX_BYTES + 1)},
                b"",
                413,
            ),
            ({"Content-Length": "2"}, b"{", 400),
            ({"Content-Length": "1"}, b"{", 400),
            ({"Content-Length": "2"}, b"[]", 400),
        )
        for headers, raw, expected_status in cases:
            with subtests.test(headers=headers, raw=raw):
                fake_self = fake_handler({}, mocker=mocker)
                fake_self.headers = {**fake_self.headers, **headers}
                fake_self.rfile = io.BytesIO(raw)
                prepare = mocker.patch.object(handoff_module, "prepare")
                control.prepare_handoff(fake_self, self.bindings)
                prepare.assert_not_called()
                fake_self.send_error.assert_called_once()
                assert fake_self.send_error.call_args.args[0] == expected_status

        unknown = fake_handler({**expected_metadata(), "unexpected": True}, mocker=mocker)
        prepare = mocker.patch.object(handoff_module, "prepare")
        control.prepare_handoff(unknown, self.bindings)
        prepare.assert_not_called()
        unknown.send_error.assert_called_once_with(400, "handoff request contains unknown fields")

    def test_handler_refuses_an_envelope_that_stalls_past_its_receive_bound(self, *, mocker):
        mocker.patch.object(handoff_protocol_module, "HANDOFF_CONTROL_RECEIVE_TIMEOUT_SECONDS", 0.2)
        prepare = mocker.patch.object(handoff_module, "prepare")
        server_side, client_side = socket.socketpair()
        # The controller ends its body after three seconds at the latest, so a reader without its
        # own bound fails with an incomplete envelope instead of hanging the test.
        end_of_body = threading.Timer(3, client_side.shutdown, args=(socket.SHUT_WR,))
        with server_side, client_side, server_side.makefile("rb") as rfile:
            handler = fake_handler(expected_metadata(), mocker=mocker)
            handler.connection = server_side
            handler.rfile = rfile
            client_side.sendall(b"{")
            end_of_body.start()
            begin = time.monotonic()
            control.prepare_handoff(handler, self.bindings)
            elapsed = time.monotonic() - begin
            end_of_body.cancel()
            end_of_body.join()
        prepare.assert_not_called()
        handler.send_error.assert_called_once_with(408, "handoff request was not received in time")
        assert elapsed < 1

    def test_handler_rejects_disk_mismatch_and_projects_non_conflict_prepare_failure(
        self, *, mocker
    ):
        mismatch = fake_handler(expected_metadata(), mocker=mocker)
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=False)
        prepare = mocker.patch.object(handoff_module, "prepare")
        control.prepare_handoff(mismatch, self.bindings)
        prepare.assert_not_called()
        mismatch.send_error.assert_called_once_with(
            409, "handoff request does not match the current disk payload"
        )

        failed = fake_handler(
            {**expected_metadata(), "timeout_seconds": "999", "lease_seconds": "17"},
            mocker=mocker,
        )
        written = []
        failed.wfile.write.side_effect = written.append
        mocker.patch.object(handoff_module, "disk_payload_matches_expected", return_value=True)
        prepare = mocker.patch.object(
            handoff_module,
            "prepare",
            side_effect=handoff_module.HandoffError("child failed"),
        )
        control.prepare_handoff(failed, self.bindings)
        assert prepare.call_args.kwargs["timeout_seconds"] == 120.0
        assert prepare.call_args.kwargs["lease_seconds"] == 17
        failed.send_response.assert_called_once_with(503)
        assert json.loads(b"".join(written))["error"] == "handoff_prepare_failed"
