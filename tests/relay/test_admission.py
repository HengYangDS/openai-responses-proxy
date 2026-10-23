"""Unit contracts for process-local request admission and drain state."""

from __future__ import annotations

from email.message import Message
from pathlib import Path
from typing import cast

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import telemetry

ROOT = Path(__file__).resolve().parents[2]


class AdmissionTests:
    def setup_method(self) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()

    def test_admission_counts_requests_without_owning_ordinary_capacity(self) -> None:
        acquired = [admission.admit_response() for _ in range(256)]
        assert acquired == [("acquired", index) for index in range(1, 257)]
        assert not hasattr(admission, "MAX_ACTIVE_RESPONSES")
        for expected in range(255, -1, -1):
            assert admission.release_response() == expected

    def test_admission_is_fail_closed_during_drain(self) -> None:
        assert admission.admit_response() == ("acquired", 1)
        assert admission.drain_snapshot() == (False, 0, 1)
        assert admission.set_draining(True, lease_seconds=30)["draining"]
        assert admission.admit_response() == ("draining", 1)
        assert admission.release_response() == 0
        admission.set_draining(False)
        assert admission.admit_response() == ("acquired", 1)
        assert admission.release_response() == 0

    def test_handlers_ids_loopback_and_lease_bounds_are_total(self) -> None:
        assert (admission.next_request_id(), admission.next_request_id()) == (1, 2)
        assert (
            admission.begin_handler(),
            admission.active_handlers(),
            admission.end_handler(),
            admission.end_handler(),
        ) == (1, 1, 0, 0)
        assert (
            admission.is_loopback_client("::1"),
            admission.is_loopback_client("invalid"),
        ) == (
            True,
            False,
        )
        for value, expected in ((None, 30), ("invalid", 30), ("0", 1), ("9999", 900)):
            assert admission.bounded_drain_lease_seconds(value) == expected

    def test_local_controller_is_a_native_loopback_request(self) -> None:
        def headers(**values: str) -> Message:
            message = Message()
            for name, value in values.items():
                message[name.replace("_", "-")] = value
            return message

        for host in ("127.0.0.1:8792", "localhost:8792", "[::1]:8792", "LOCALHOST"):
            assert admission.is_local_controller("127.0.0.1", headers(Host=host)), host
        refused = (
            ("192.0.2.1", headers(Host="127.0.0.1:8792")),
            ("127.0.0.1", headers()),
            ("127.0.0.1", headers(Host="example.invalid:8792")),
            ("127.0.0.1", headers(Host="127.0.0.1.example.invalid")),
            ("127.0.0.1", headers(Host="127.0.0.1:8792", Origin="https://example.invalid")),
            ("127.0.0.1", headers(Host="127.0.0.1:8792", Origin="null")),
        )
        for address, message in refused:
            assert not admission.is_local_controller(address, message), (address, message.items())

    def test_drain_lease_expiry_is_observable(self, *, mocker) -> None:
        mocker.patch.object(admission.time, "monotonic", side_effect=[10.0, 10.0, 12.1, 12.1, 12.1])
        admission.set_draining(True, lease_seconds=2)
        assert admission.drain_snapshot() == (False, 2, 0)
        counters = cast(
            "dict[str, int]",
            telemetry.status(
                release="1",
                serving_payload_sha256=None,
                release_receipt_sha256=None,
                admission=admission.snapshot(),
                runtime_identity={},
            )["counters"],
        )
        assert counters["drain_leases_expired"] == 1
