"""Successor handoff health observation contracts."""

from __future__ import annotations

from pathlib import Path

from tests.serving.handoff.fixtures import child_pid_matching_health
from tests.serving.handoff.fixtures import expected_metadata
from tests.serving.handoff.fixtures import matching_health

ROOT = Path(__file__).resolve().parents[3]


class TestSuccessorHealthObservation:
    """Exact successor states accepted by the subprocess health observer."""

    def test_successor_observer_accepts_the_stable_finalized_health_state(self, *, mocker):
        expected = expected_metadata()
        health = matching_health(54321, expected, handoff_state="finalized")
        mocker.patch("tests.serving.handoff.fixtures.http_json", return_value=(200, health))
        observed = child_pid_matching_health(8791, expected, exclude_pid=12345)

        assert observed == 54321

    def test_successor_observer_rejects_non_successor_health(self, subtests, *, mocker):
        expected = expected_metadata()
        cases = (
            matching_health(54321, expected, handoff_state="idle"),
            matching_health(54321, expected, release="wrong-release"),
            matching_health(0, expected),
            matching_health(True, expected),
        )

        for health in cases:
            mocker.patch("tests.serving.handoff.fixtures.http_json", return_value=(200, health))
            with subtests.test(health=health):
                assert child_pid_matching_health(8791, expected, exclude_pid=12345) is None
