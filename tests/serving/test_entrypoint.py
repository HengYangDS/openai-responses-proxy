"""Executable listener bootstrap contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.serving import entrypoint
from openai_responses_proxy.serving import identity
from tests.lifecycle.fixtures import FIXTURE_GENERATION

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# The payload of the running process: one generation of the control root "/installed".
_PAYLOAD = Path("/installed/generations", FIXTURE_GENERATION)
_EXECUTABLE = _PAYLOAD / "bin/openai-responses-proxy"


class TestListenerEntrypoint:
    def teardown_method(self) -> None:
        entrypoint._PROCESS.bootstrap = None

    def test_bootstrap_freezes_one_identity_between_two_identical_checks(
        self, *, mocker: MockerFixture
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "generations", FIXTURE_GENERATION)
            executable = root / "bin/openai-responses-proxy"
            loaded = identity.LoadedPayloadIdentity("2.0.8", "a" * 64, "b" * 64, "c" * 64, root)
            freeze = mocker.patch.object(
                entrypoint.identity,
                "freeze_loaded_payload",
                side_effect=(loaded, loaded),
            )

            result = entrypoint.bootstrap(executable)

        assert result == entrypoint.Bootstrap(executable, loaded)
        assert freeze.call_args_list == [
            mocker.call(executable),
            mocker.call(executable),
        ]

    def test_bootstrap_rejects_missing_and_changed_payload_identity(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        executable = _EXECUTABLE
        loaded = identity.LoadedPayloadIdentity(
            "2.0.8", "a" * 64, "b" * 64, "c" * 64, executable.parents[1]
        )
        cases = (
            ((None,), "installed payload identity is unavailable"),
            ((loaded, None), "installed payload identity changed during startup"),
        )
        for identities, message in cases:
            with subtests.test(message=message):
                mocker.patch.object(
                    entrypoint.identity,
                    "freeze_loaded_payload",
                    side_effect=identities,
                )
                with pytest.raises(RuntimeError, match=message):
                    entrypoint.bootstrap(executable)

    def _admit_run(self, mocker: MockerFixture) -> None:
        loaded = identity.LoadedPayloadIdentity("2.0.8", "a" * 64, "b" * 64, "c" * 64, _PAYLOAD)
        mocker.patch.object(
            entrypoint, "bootstrap", return_value=entrypoint.Bootstrap(_EXECUTABLE, loaded)
        )

    def test_handoff_context_launches_the_selected_generation(
        self, *, mocker: MockerFixture
    ) -> None:
        """A running predecessor resolves the candidate selected by the installer."""
        self._admit_run(mocker)
        entrypoint._PROCESS.bootstrap = entrypoint.bootstrap()
        selected = Path(
            "/installed/generations/11111111111111111111111111111111/bin/openai-responses-proxy"
        )
        resolve = mocker.patch.object(
            entrypoint.identity,
            "selected_payload_executable",
            return_value=selected,
        )
        committed = mocker.patch.object(
            entrypoint.identity,
            "committed_payload",
            return_value=mocker.sentinel.selected_identity,
        )

        context = entrypoint._handoff_context()

        assert context.successor_executable() == selected
        assert context.committed_payload() is mocker.sentinel.selected_identity
        assert resolve.call_args_list == [
            mocker.call(entrypoint._PROCESS.bootstrap.executable),
            mocker.call(entrypoint._PROCESS.bootstrap.executable),
        ]
        committed.assert_called_once_with(selected)

    def test_main_delegates_handoff_child(self, *, mocker: MockerFixture) -> None:
        self._admit_run(mocker)
        context = mocker.sentinel.handoff_context
        mocker.patch.object(entrypoint, "_handoff_context", return_value=context)
        run_child = mocker.patch.object(entrypoint.handoff, "run_child", return_value=7)
        assert entrypoint.run(handoff_child=True) == 7
        run_child.assert_called_once_with(context)

    def test_main_closes_normal_server_after_keyboard_interrupt(
        self, *, mocker: MockerFixture
    ) -> None:
        self._admit_run(mocker)
        listener = mocker.Mock()
        context = mocker.sentinel.handoff_context
        mocker.patch.object(entrypoint, "create_server", return_value=listener)
        mocker.patch.object(entrypoint, "_handoff_context", return_value=context)
        log = mocker.patch.object(entrypoint.operational_log, "log")
        serve = mocker.patch.object(
            entrypoint.handoff,
            "serve_with_resume",
            side_effect=KeyboardInterrupt,
        )
        assert entrypoint.run() == 0
        log.assert_called_once()
        fields = dict(field.split("=", 1) for field in log.call_args.args[0].split())
        assert fields["event"] == "listener_starting"
        assert "listener" in fields
        assert {
            "upstream_timeout",
            "read_timeout",
            "log_max_bytes",
            "log_backup_count",
        } <= fields.keys()
        serve.assert_called_once_with(listener, context)
        listener.server_close.assert_called_once_with()
        assert entrypoint._PROCESS.server is listener

    def test_invalid_runtime_configuration_exits_without_starting_a_server(
        self, *, mocker: MockerFixture
    ) -> None:
        self._admit_run(mocker)
        mocker.patch.object(
            entrypoint.runtime_config,
            "listener_port",
            side_effect=entrypoint.runtime_config.ConfigurationError("invalid port"),
        )
        create_server = mocker.patch.object(entrypoint, "create_server")
        log = mocker.patch.object(entrypoint.operational_log, "log")

        assert entrypoint.run() == 2

        create_server.assert_not_called()
        log.assert_called_once_with("event=configuration_error exception=ConfigurationError")

    def test_invalid_payload_exits_without_reading_runtime_configuration(
        self, *, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(entrypoint, "bootstrap", side_effect=RuntimeError("untrusted"))
        listener_port = mocker.patch.object(entrypoint.runtime_config, "listener_port")
        log = mocker.patch.object(entrypoint.operational_log, "log")

        assert entrypoint.run() == 2

        listener_port.assert_not_called()
        log.assert_called_once_with("event=payload_identity_error exception=RuntimeError")


class ServiceRuntimeIdentityContracts:
    def test_source_tree_without_an_installed_manifest_has_no_release_claim(self) -> None:
        assert entrypoint.release_version() == "0+unknown"

    def test_runtime_status_reports_loaded_serving_payload_sha256(self) -> None:
        identity = entrypoint.runtime_status()["serving_payload_sha256"]
        if entrypoint._PROCESS.bootstrap is None:
            assert identity is None
        else:
            assert identity == entrypoint._PROCESS.bootstrap.payload.serving_payload_sha256
