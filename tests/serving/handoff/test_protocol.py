"""Rolling-handoff wire protocol and transition contracts."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.serving import inventory
from tests.serving.handoff import fixtures as handoff_fixtures
from tests.serving.handoff.fixtures import entrypoint_module
from tests.serving.handoff.fixtures import handoff_module
from tests.serving.handoff.fixtures import http_json
from tests.serving.handoff.fixtures import runtime_state_module

ROOT = Path(__file__).resolve().parents[3]


class TestProtocolContract:
    """The wire-level contract every other component below depends on."""

    def setup_method(self):
        self.p = entrypoint_module
        runtime_state_module.reset_for_test()
        handoff_module.reset_session_to_idle()

    def test_parent_and_controller_declare_protocol_version_two(self):
        assert (
            handoff_module.HANDOFF_PROTOCOL_VERSION,
            handoff.HANDOFF_PROTOCOL_VERSION,
        ) == (2, 2)

    def test_runtime_status_contains_full_handoff_health_shape(self):
        status = self.p.runtime_status()
        required = (
            "pid",
            "handoff_protocol_version",
            "handoff_transaction_id",
            "handoff_state",
            "handoff_capabilities",
            "release",
            "serving_payload_sha256",
            "release_receipt_sha256",
            "payload_manifest_sha256",
            "accepting",
            "draining",
        )
        assert set(required).difference(status) == set()
        assert status["handoff_protocol_version"] == 2
        assert status["handoff_capabilities"] == [
            handoff_module.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_module.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ]
        assert status["pid"] == os.getpid()

    def test_runtime_status_reports_an_accepting_idle_state(self):
        status = self.p.runtime_status()
        assert (
            status["accepting"],
            status["draining"],
            status["handoff_transaction_id"],
        ) == (
            True,
            False,
            None,
        )
        assert status["handoff_state"] in (None, "idle")

    def test_a_prepared_or_committing_handoff_keeps_request_admission_open(
        self,
    ):
        def assert_accepting(state):
            handoff_module._HANDOFF_SESSION["state"] = state
            try:
                status = self.p.runtime_status()
                assert (status["draining"], status["accepting"]) == (False, True)
            finally:
                handoff_module.reset_session_to_idle()

        for state in ("preparing", "ready", "committing", "serving", "finalizing"):
            assert_accepting(state)

    def test_healthz_over_real_loopback_http_exposes_the_same_shape(self):
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "https://gateway.example/v1")}
        )
        proxy = self.p.create_server(("127.0.0.1", 0), providers=providers)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            status_code, payload = http_json(proxy.server_address[1], "/healthz")
            assert status_code == 200
            required = (
                "pid",
                "handoff_protocol_version",
                "handoff_transaction_id",
                "handoff_state",
                "handoff_capabilities",
                "payload_manifest_sha256",
                "accepting",
            )
            assert set(required).difference(payload) == set()
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)


class TestHandoffTransitionValidation:
    def test_transition_table_matches_the_documented_protocol(self):
        allowed = frozenset(
            (
                ("idle", "preparing"),
                ("preparing", "ready"),
                ("ready", "committing"),
                ("committing", "serving"),
                ("serving", "finalizing"),
                ("finalizing", "finalized"),
                ("finalized", "idle"),
                ("aborting", "rolled_back"),
                ("rolled_back", "idle"),
                *tuple(
                    (state, "aborting")
                    for state in (
                        "preparing",
                        "ready",
                        "committing",
                        "serving",
                        "finalizing",
                    )
                ),
            )
        )
        states = {state for transition in allowed for state in transition}
        results = {
            (current, target): handoff_module.validate_transition(current, target)
            for current in states
            for target in states
        }
        assert {transition for transition, valid in results.items() if valid} == allowed


def test_initial_proxy_spawn_avoids_multithreaded_posix_fork(tmp_path: Path, mocker) -> None:
    """A mocked launch contract must not build or prewarm a real native payload."""
    payload = tmp_path / "generation"
    payload.mkdir()
    upstream = "http://127.0.0.1:43123"
    (payload / inventory.PROVIDER_MANIFEST).write_text(
        f'version = 1\n\n[providers.dmxapi]\nbase_url = "{upstream}"\npolicy = "dmxapi"\n',
        encoding="utf-8",
    )
    ctx = runtime_context.RuntimeContext(
        install_dir=str(tmp_path / "payload"),
        executable=str(payload / "bin" / "proxy"),
        command=str(tmp_path / "command"),
        log_dir=str(tmp_path / "state"),
        user_home=str(tmp_path),
        port=43124,
    )
    build = mocker.patch.object(handoff_fixtures, "write_installed_payload")
    mocker.patch.dict(handoff_fixtures.os.environ, {"OPENAI_RESPONSES_PROXY_EXECUTABLE": "foreign"})
    child = mocker.Mock()
    child.poll.return_value = None
    mocker.patch.object(handoff_fixtures, "proxy_is_up", return_value=True)
    popen = mocker.patch.object(handoff_fixtures.subprocess, "Popen", return_value=child)
    try:
        started = handoff_fixtures.start_real_proxy(
            ctx, upstream_url=upstream, log_path=tmp_path / "proxy.log"
        )
    finally:
        mocker.stop(popen)

    assert started is child
    build.assert_not_called()
    assert popen.call_args.kwargs["close_fds"] is True
    environment = popen.call_args.kwargs["env"]
    assert environment["OPENAI_RESPONSES_PROXY_HOME"] == ctx.install_dir
    assert environment["OPENAI_RESPONSES_PROXY_STATE_HOME"] == str(tmp_path / "state")
    assert environment["OPENAI_RESPONSES_PROXY_EXECUTABLE"] == ctx.executable
