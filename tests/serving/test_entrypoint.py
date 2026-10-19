"""Executable listener bootstrap contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.serving import entrypoint
from openai_responses_proxy.serving import identity


class TestListenerEntrypoint:
    def teardown_method(self) -> None:
        entrypoint._BOOTSTRAP = None

    def test_bootstrap_loads_routes_between_two_identical_identity_checks(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "bin/openai-responses-proxy"
            loaded = identity.LoadedPayloadIdentity("2.0.8", "a" * 64, "b" * 64, "c" * 64, root)
            providers = provider_registry.Registry(
                {"fixture": provider_registry.Profile("fixture", "http://127.0.0.1:43123/v1")}
            )
            freeze = mocker.patch.object(
                entrypoint.identity,
                "freeze_loaded_payload",
                side_effect=(loaded, loaded),
            )
            load = mocker.patch.object(entrypoint.provider_registry, "load", return_value=providers)

            result = entrypoint.bootstrap(executable)

        assert result == entrypoint.Bootstrap(executable, loaded, providers)
        assert freeze.call_args_list == [
            mocker.call(executable),
            mocker.call(executable),
        ]
        load.assert_called_once_with(root / "providers.toml")

    def test_bootstrap_rejects_missing_and_changed_payload_identity(
        self, subtests, *, mocker
    ) -> None:
        executable = Path("/installed/bin/openai-responses-proxy")
        loaded = identity.LoadedPayloadIdentity(
            "2.0.8", "a" * 64, "b" * 64, "c" * 64, executable.parents[1]
        )
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "https://gateway.example/v1")}
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
                mocker.patch.object(entrypoint.provider_registry, "load", return_value=providers)
                with pytest.raises(RuntimeError, match=message):
                    entrypoint.bootstrap(executable)

    def _admit_run(self, mocker) -> None:
        loaded = identity.LoadedPayloadIdentity(
            "2.0.8", "a" * 64, "b" * 64, "c" * 64, Path("/installed")
        )
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "https://gateway.example/v1")}
        )
        mocker.patch.object(
            entrypoint,
            "bootstrap",
            return_value=entrypoint.Bootstrap(
                Path("/installed/bin/openai-responses-proxy"), loaded, providers
            ),
        )

    def test_runtime_providers_fail_closed_until_bootstrap_and_then_return_frozen_routes(
        self, *, mocker
    ) -> None:
        with pytest.raises(RuntimeError, match="has not verified"):
            entrypoint.runtime_providers()
        loaded = identity.LoadedPayloadIdentity(
            "2.0.8", "a" * 64, "b" * 64, "c" * 64, Path("/installed")
        )
        providers = mocker.sentinel.providers
        entrypoint._BOOTSTRAP = entrypoint.Bootstrap(
            Path("/installed/bin/openai-responses-proxy"), loaded, providers
        )
        assert entrypoint.runtime_providers() is providers

    def test_handoff_context_launches_the_selected_generation(self, *, mocker) -> None:
        """A running predecessor resolves the candidate selected by the installer."""
        self._admit_run(mocker)
        entrypoint._BOOTSTRAP = entrypoint.bootstrap()
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
            mocker.call(entrypoint._BOOTSTRAP.executable),
            mocker.call(entrypoint._BOOTSTRAP.executable),
        ]
        committed.assert_called_once_with(selected)

    def test_main_delegates_handoff_child(self, *, mocker) -> None:
        self._admit_run(mocker)
        context = mocker.sentinel.handoff_context
        mocker.patch.object(entrypoint, "_handoff_context", return_value=context)
        run_child = mocker.patch.object(entrypoint.handoff, "run_child", return_value=7)
        assert entrypoint.run(handoff_child=True) == 7
        run_child.assert_called_once_with(context)

    def test_main_closes_normal_server_after_keyboard_interrupt(self, *, mocker) -> None:
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
        assert entrypoint._SERVER_INSTANCE is listener

    def test_invalid_runtime_configuration_exits_without_starting_a_server(self, *, mocker) -> None:
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

    def test_invalid_payload_exits_without_reading_runtime_configuration(self, *, mocker) -> None:
        mocker.patch.object(entrypoint, "bootstrap", side_effect=RuntimeError("untrusted"))
        listener_port = mocker.patch.object(entrypoint.runtime_config, "listener_port")
        log = mocker.patch.object(entrypoint.operational_log, "log")

        assert entrypoint.run() == 2

        listener_port.assert_not_called()
        log.assert_called_once_with("event=payload_identity_error exception=RuntimeError")

    def test_created_server_owns_the_exact_provider_registry(self, *, mocker) -> None:
        providers = mocker.sentinel.providers
        listener = entrypoint.create_server(("127.0.0.1", 0), providers=providers)
        try:
            assert listener.bindings.providers is providers
        finally:
            listener.server_close()

    def test_runtime_providers_require_a_verified_bootstrap(self) -> None:
        entrypoint._BOOTSTRAP = None
        with pytest.raises(RuntimeError, match="has not verified"):
            entrypoint.runtime_providers()


class ServiceRuntimeIdentityContracts:
    def test_source_tree_without_an_installed_manifest_has_no_release_claim(self):
        assert entrypoint.release_version() == "0+unknown"

    def test_runtime_status_reports_loaded_serving_payload_sha256(self):
        identity = entrypoint.runtime_status()["serving_payload_sha256"]
        if entrypoint._BOOTSTRAP is None:
            assert identity is None
        else:
            assert identity == entrypoint._BOOTSTRAP.payload.serving_payload_sha256
