"""Unit contracts for process-local request admission."""

from __future__ import annotations

from email.message import Message
from pathlib import Path

from openai_responses_proxy.relay import admission

ROOT = Path(__file__).resolve().parents[2]


class AdmissionTests:
    def setup_method(self) -> None:
        admission.reset_for_test()

    def test_admission_counts_requests_without_owning_ordinary_capacity(self) -> None:
        admitted = [admission.admit_response() for _ in range(256)]
        assert admitted == list(range(1, 257))
        assert admission.snapshot() == {"active_responses": 256}
        assert not hasattr(admission, "MAX_ACTIVE_RESPONSES")
        for expected in range(255, -1, -1):
            assert admission.release_response() == expected

    def test_handlers_ids_and_loopback_are_total(self) -> None:
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
