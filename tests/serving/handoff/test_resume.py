"""Post-handoff serve and resume contracts."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from tests.serving.handoff.fixtures import HandoffFixture
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import handoff_outcome_ready

ROOT = Path(__file__).resolve().parents[3]


class TestServeWithHandoffResume(HandoffFixture):
    def test_rollback_outcome_serves_again_on_the_same_socket(self, *, mocker):
        server = mocker.Mock()
        calls = []

        def fake_serve_forever():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                self.p._HANDOFF_SESSION["outcome"] = "rolled_back"
                handoff_outcome_ready().set()
            else:
                self.p._HANDOFF_SESSION["outcome"] = None
                handoff_outcome_ready().set()

        server.serve_forever.side_effect = fake_serve_forever
        self.p.serve_with_resume(server, self.context)
        assert server.serve_forever.call_count == 2

    def test_waits_for_the_outcome_ready_event_instead_of_trusting_stale_state(self, *, mocker):
        # ``server.shutdown()`` returning on the request thread races with the
        # background coordinator thread still finishing its work; the resume
        # loop must not read ``outcome`` the instant ``serve_forever()``
        # returns, or it could observe a stale ``None``/pre-commit value.
        server = mocker.Mock()
        calls = []

        def fake_serve_forever():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                self.p._HANDOFF_SESSION["state"] = "committing"

                def delayed_outcome():
                    time.sleep(0.05)
                    self.p._HANDOFF_SESSION["outcome"] = "rolled_back"
                    handoff_outcome_ready().set()

                threading.Thread(target=delayed_outcome, daemon=True).start()
            else:
                self.p._HANDOFF_SESSION["outcome"] = None
                handoff_outcome_ready().set()

        server.serve_forever.side_effect = fake_serve_forever
        self.p.serve_with_resume(server, self.context)
        assert server.serve_forever.call_count == 2, (
            "resume loop did not wait for the delayed rollback outcome"
        )

    def test_terminal_outcomes_stop_serving_and_log_only_unconfirmed_states(
        self, subtests, *, mocker
    ):
        for outcome, expected_log in (
            ("finalized", None),
            (None, None),
            ("abort_unconfirmed", "handoff_abort_unconfirmed"),
            ("unknown", "handoff_outcome_unconfirmed"),
        ):
            with subtests.test(outcome=outcome):
                self.p.reset_session_to_idle()
                logs = []
                context = entrypoint_module._handoff_context()
                object.__setattr__(context, "log", logs.append)
                server = mocker.Mock()

                def serve(outcome=outcome):
                    self.p._HANDOFF_SESSION["outcome"] = outcome
                    handoff_outcome_ready().set()

                server.serve_forever.side_effect = serve
                self.p.serve_with_resume(server, context)
                server.serve_forever.assert_called_once()
                assert bool(logs and expected_log in logs[0]) == bool(expected_log)

    def test_finalized_outcome_waits_for_active_work_until_deadline(self, *, mocker):
        server = mocker.Mock()
        active = iter((2, 0))
        context = entrypoint_module._handoff_context()
        object.__setattr__(context, "active_responses", lambda: next(active))
        object.__setattr__(context, "active_handlers", lambda: 0)
        logs = []
        object.__setattr__(context, "log", logs.append)

        server.serve_forever.side_effect = lambda: (
            self.p._HANDOFF_SESSION.update(
                outcome="finalized", drain_deadline=time.monotonic() + 10
            ),
            handoff_outcome_ready().set(),
        )
        mocker.patch.object(self.p.time, "sleep", return_value=None)
        self.p.serve_with_resume(server, context)
        assert logs == []

        context = entrypoint_module._handoff_context()
        object.__setattr__(context, "active_responses", lambda: 3)
        object.__setattr__(context, "active_handlers", lambda: 0)
        logs = []
        object.__setattr__(context, "log", logs.append)
        self.p._HANDOFF_SESSION.update(outcome="finalized", drain_deadline=time.monotonic() - 1)
        server.serve_forever.side_effect = lambda: handoff_outcome_ready().set()
        self.p.serve_with_resume(server, context)
        assert "remaining_active=3" in logs[0]

    def test_initial_serving_thread_is_joined_before_outcome_projection(self, *, mocker):
        initial = mocker.Mock()
        server = mocker.Mock()
        self.p._HANDOFF_SESSION.update(outcome=None, state="idle")
        self.p.serve_with_resume(server, self.context, initial_serving_thread=initial)
        initial.join.assert_called_once()
        server.serve_forever.assert_not_called()
