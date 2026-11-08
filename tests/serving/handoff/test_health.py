"""Successor handoff health observation contracts."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.serving.handoff import protocol as handoff_protocol_module
from tests.serving.handoff.fixtures import child_pid_matching_health
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import fake_child
from tests.serving.handoff.fixtures import matching_health

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

ROOT = Path(__file__).resolve().parents[3]


class TestSuccessorHealthObservation:
    """Exact successor states accepted by the subprocess health observer."""

    def test_successor_observer_accepts_the_stable_finalized_health_state(
        self, *, mocker: MockerFixture
    ) -> None:
        expected = expected_metadata()
        health = matching_health(54321, expected, handoff_state="finalized")
        mocker.patch("tests.serving.handoff.fixtures.http_json", return_value=(200, health))
        observed = child_pid_matching_health(8791, expected, exclude_pid=12345)

        assert observed == 54321

    def test_successor_observer_rejects_non_successor_health(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        expected = expected_metadata()
        cases = (
            matching_health(54321, expected, handoff_state="idle"),
            matching_health(54321, expected, release="wrong-release"),
            matching_health(0, expected),
            matching_health(child_or_pid=True, expected=expected),
        )

        for health in cases:
            mocker.patch("tests.serving.handoff.fixtures.http_json", return_value=(200, health))
            with subtests.test(health=health):
                assert child_pid_matching_health(8791, expected, exclude_pid=12345) is None


class TestParentHealthProbe:
    """The parent reads bounded health snapshots until the exact successor serves."""

    def test_probe_health_rejects_oversized_invalid_and_non_object_payloads(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        response = mocker.MagicMock()
        response.__enter__.return_value = response
        mocker.patch.object(
            handoff_protocol_module.loopback,
            "open_request",
            return_value=response,
        )
        for payload, error in (
            (
                b"x" * (handoff_protocol_module.HANDOFF_CONTROL_MAX_BYTES + 1),
                "exceeds the control limit",
            ),
            (b"{", "response is invalid"),
            (b"[]", "must be an object"),
        ):
            with subtests.test(error=error):
                response.read.return_value = payload
                with pytest.raises(handoff_protocol_module.HandoffError, match=error):
                    handoff_protocol_module.probe_health(8791, timeout_seconds=1)
        response.read.return_value = b'{"ok":true}'
        assert handoff_protocol_module.probe_health(8791, timeout_seconds=1) == {"ok": True}

    def test_probe_health_retries_until_the_successor_serves_the_expected_identity(
        self, *, mocker: MockerFixture
    ) -> None:
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        old = {**matching_health(child, expected), "pid": child.runtime_pid - 1}
        current = matching_health(child, expected)
        mocker.patch.object(
            handoff_protocol_module,
            "_read_health",
            side_effect=(old, current),
        )
        mocker.patch.object(
            handoff_protocol_module.time,
            "monotonic",
            side_effect=(0.0, 0.1, 0.2),
        )
        mocker.patch.object(handoff_protocol_module.time, "sleep")

        assert (
            handoff_protocol_module.probe_health(
                8791,
                timeout_seconds=1,
                expected={
                    "pid": child.runtime_pid,
                    "release": expected["release"],
                },
            )
            == current
        )

    def test_probe_health_accepts_observability_fields_beside_the_exact_identity(
        self, *, mocker: MockerFixture
    ) -> None:
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        identity = matching_health(child, expected)
        observed = {
            **identity,
            "uptime_seconds": 12.5,
            "active_responses": 0,
            "active_handlers": 1,
            "counters": {},
            "upstream_classifications": {},
            "last_failure": None,
        }
        mocker.patch.object(handoff_protocol_module, "_read_health", return_value=observed)
        mocker.patch.object(handoff_protocol_module.time, "sleep")

        assert (
            handoff_protocol_module.probe_health(8791, timeout_seconds=1, expected=identity)
            == observed
        )

    def test_probe_health_retries_failed_observations_until_exact_identity(
        self, *, mocker: MockerFixture
    ) -> None:
        expected = expected_metadata()
        child = fake_child(mocker=mocker)
        current = matching_health(child, expected)
        mocker.patch.object(
            handoff_protocol_module,
            "_read_health",
            side_effect=(
                struct.error("transient observer failure"),
                current,
            ),
        )
        mocker.patch.object(
            handoff_protocol_module.time,
            "monotonic",
            side_effect=(0.0, 0.1, 0.2),
        )
        mocker.patch.object(handoff_protocol_module.time, "sleep")

        assert (
            handoff_protocol_module.probe_health(
                8791,
                timeout_seconds=1,
                expected={
                    "pid": child.runtime_pid,
                    "release": expected["release"],
                },
            )
            == current
        )
