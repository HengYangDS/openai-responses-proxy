"""Contracts for the portable deployed-runtime context."""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
from typing import TypedDict

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import roles

ROOT = Path(__file__).resolve().parents[2]


class _RuntimeOverrides(TypedDict, total=False):
    port: int
    proxy_log_max_bytes: int
    proxy_log_backup_count: int
    watchdog_log_max_bytes: int
    watchdog_log_backup_count: int


class TestRuntimeContext:
    def test_service_identity_preserves_default_and_isolates_alternate_roots(self):
        canonical = config.default_data_dir({})
        assert context.service_id(canonical) == context.SERVICE_ID
        isolated = context.service_id(str(Path(canonical).with_name("proxy-validation")))
        assert isolated.startswith(context.SERVICE_ID + ".")
        assert isolated == context.service_id(str(Path(canonical).with_name("proxy-validation")))
        assert isolated != context.service_id(str(Path(canonical).with_name("another-validation")))

    def test_current_executable_uses_the_actual_process_invocation(
        self, tmp_path, *, mocker
    ) -> None:
        invoked = tmp_path / "copied-proxy"
        invoked.write_text("fixture", encoding="utf-8")
        frozen = mocker.patch.object(roles.sys, "frozen", True, create=True)
        mocker.patch.object(roles.sys, "executable", str(invoked))
        assert roles.current_executable() == str(invoked.resolve())
        mocker.stop(frozen)

        mocker.patch.object(roles.sys, "argv", [str(invoked)])
        assert roles.current_executable() == str(invoked.resolve())

    def test_default_port_is_8792_and_explicit_override_remains_configurable(self):
        assert config.DEFAULT_PORT == 8792
        assert config.listener_port({}) == 8792
        assert config.listener_port({config.PROXY_PORT_ENV: "8808"}) == 8808

        projected = context.RuntimeContext(
            install_dir="/opt/proxy",
            executable="/opt/proxy/bin/openai-responses-proxy",
            command="/fixture/user-root/.local/bin/openai-responses-proxy",
            log_dir="/var/state/proxy",
            port=8808,
        )
        assert projected.port == 8808

    def test_payload_root_follows_executable_platform_syntax(self, subtests) -> None:
        for executable, expected in (
            (
                "/opt/proxy/generations/abc/bin/openai-responses-proxy",
                "/opt/proxy/generations/abc",
            ),
            (
                r"C:\proxy\generations\abc\bin\openai-responses-proxy.exe",
                r"C:\proxy\generations\abc",
            ),
        ):
            with subtests.test(executable=executable):
                projected = context.RuntimeContext(
                    install_dir="/unused/control-root",
                    executable=executable,
                    command="/unused/command",
                    log_dir="/unused/logs",
                )
                assert projected.payload_dir == expected

    def test_context_is_the_single_deployed_runtime_projection(self, *, mocker):
        mocker.patch.object(config, "home_dir", return_value="/portable/home")
        mocker.patch.object(config, "data_dir", return_value="/portable/payload")
        mocker.patch.object(config, "state_dir", return_value="/portable/state")
        projected = context.create(executable="/portable/bin/openai-responses-proxy", port=8808)

        assert projected.install_dir == "/portable/payload"
        assert projected.executable == "/portable/bin/openai-responses-proxy"
        assert projected.log_dir == "/portable/state"
        assert projected.user_home == "/portable/home"
        assert projected.port == 8808

    def test_runtime_spec_derives_process_settings_from_one_contract(self, tmp_path):
        install_dir = tmp_path / "payload"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(install_dir / "bin" / "openai-responses-proxy"),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "state"),
            port=8808,
            upstream_timeout=45.0,
        )

        environment = runtime_spec.environment(runtime_spec.write(projected))
        carrier = json.loads(runtime_spec.path(projected).read_text(encoding="utf-8"))

        assert carrier["schema_version"] == 1
        assert "user_home" not in carrier
        assert environment[config.HOME_ENV] == str(install_dir)
        assert environment[config.STATE_HOME_ENV] == str(tmp_path / "state")
        assert environment[config.PROXY_PORT_ENV] == "8808"
        assert "OPENAI_RESPONSES_PROXY_EXECUTABLE" not in environment
        assert environment[config.PROXY_LOG_ENV] == str(tmp_path / "state" / "proxy.log")
        assert environment[config.UPSTREAM_TIMEOUT_ENV] == "45.0"

    def test_runtime_spec_accepts_an_immutable_generation_carrier(self, tmp_path) -> None:
        install_dir = tmp_path / "payload"
        generation_dir = install_dir / "generations" / "release-identity"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(generation_dir / "bin" / "openai-responses-proxy"),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "state"),
            port=8808,
        )

        environment = runtime_spec.environment(runtime_spec.write(projected))

        assert runtime_spec.path(projected) == generation_dir / runtime_spec.FILENAME
        assert environment[config.HOME_ENV] == str(install_dir)
        assert environment[config.PROXY_PORT_ENV] == "8808"

    def test_explicit_upstream_survives_native_carrier_and_context_reload(
        self, tmp_path, *, mocker
    ) -> None:
        install_dir = tmp_path / "payload"
        executable = install_dir / "bin" / "openai-responses-proxy"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(executable),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "state"),
            upstream_base_url="http://127.0.0.1:43123/v1",
        )
        target = runtime_spec.write(projected)
        environment = runtime_spec.environment(target)
        mocker.patch.object(config, "home_dir", return_value=str(tmp_path))
        mocker.patch.object(config, "data_dir", return_value=str(install_dir))
        mocker.patch.object(config, "state_dir", return_value=str(tmp_path / "state"))

        restored = context.create(executable=str(executable))

        assert environment[config.UPSTREAM_BASE_URL_ENV] == projected.upstream_base_url
        assert restored.upstream_base_url == projected.upstream_base_url
        assert (
            context.create(
                executable=str(executable), upstream_base_url="https://api.openai.com/v1"
            ).upstream_base_url
            == "https://api.openai.com/v1"
        )

    def test_explicit_upstream_refuses_credentials_before_native_materialization(
        self, tmp_path, *, mocker
    ) -> None:
        mocker.patch.object(config, "data_dir", return_value=str(tmp_path / "payload"))
        with pytest.raises(errors.InstallError, match="absolute HTTP\\(S\\) URL"):
            context.create(upstream_base_url="https://user:secret@api.openai.com/v1")

    def test_runtime_spec_accepts_the_stable_carrier_written_by_the_previous_release(
        self, tmp_path
    ) -> None:
        install_dir = tmp_path / "payload"
        target = install_dir / runtime_spec.FILENAME
        target.parent.mkdir(parents=True)
        target.write_bytes(
            digest.canonical_json(
                {
                    "schema_version": 1,
                    "install_dir": str(install_dir),
                    "log_dir": str(tmp_path / "state"),
                    "port": 8808,
                    "proxy_log_max_bytes": config.DEFAULT_PROXY_LOG_MAX_BYTES,
                    "proxy_log_backup_count": config.DEFAULT_PROXY_LOG_BACKUP_COUNT,
                    "watchdog_log_max_bytes": config.DEFAULT_WATCHDOG_LOG_MAX_BYTES,
                    "watchdog_log_backup_count": config.DEFAULT_WATCHDOG_LOG_BACKUP_COUNT,
                    "upstream_timeout": config.DEFAULT_UPSTREAM_TIMEOUT,
                    "upstream_read_timeout": config.DEFAULT_UPSTREAM_READ_TIMEOUT,
                    "watchdog_interval": config.DEFAULT_WATCHDOG_INTERVAL,
                    "watchdog_max_backoff": config.DEFAULT_WATCHDOG_MAX_BACKOFF,
                    "response_failed_compaction_budget": (
                        config.DEFAULT_RESPONSE_FAILED_COMPACTION_BUDGET
                    ),
                    "response_failed_max_stages": config.DEFAULT_RESPONSE_FAILED_MAX_STAGES,
                }
            )
        )

        environment = runtime_spec.environment(target)

        assert environment[config.HOME_ENV] == str(install_dir)
        assert environment[config.STATE_HOME_ENV] == str(tmp_path / "state")
        assert environment[config.PROXY_PORT_ENV] == "8808"

    def test_posix_projection_is_not_reinterpreted_by_a_windows_host(self, tmp_path, *, mocker):
        install_dir = tmp_path / "posix-payload"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(install_dir / "bin" / "openai-responses-proxy"),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "posix-state"),
        )
        mocker.patch.object(config.os, "path", ntpath)
        mocker.patch.object(config, "home_dir", return_value="/portable/home")
        mocker.patch.object(config, "data_dir", return_value="/portable/payload")
        mocker.patch.object(config, "state_dir", return_value="/portable/state")
        environment = runtime_spec.environment(runtime_spec.write(projected))
        created = context.create(executable="/portable/bin/openai-responses-proxy")
        assert environment[config.PROXY_LOG_ENV] == str(tmp_path / "posix-state" / "proxy.log")
        assert created.executable == "/portable/bin/openai-responses-proxy"

    def test_path_projection_preserves_windows_roots_and_absolutizes_relative_overrides(
        self,
    ) -> None:
        assert config.path_join(r"C:\portable\proxy", "state") == r"C:\portable\proxy\state"
        relative = config._absolute_override({config.HOME_ENV: "payload"}, config.HOME_ENV)
        assert relative == str((Path.cwd() / "payload").resolve())

    def test_native_runtime_projects_only_the_installed_executable(self, tmp_path, *, mocker):
        payload = tmp_path / "payload"
        mocker.patch.object(config, "home_dir", return_value="/portable/home")
        mocker.patch.object(config, "data_dir", return_value=str(payload))
        mocker.patch.object(config, "state_dir", return_value=str(tmp_path / "state"))
        executable = payload / "bin" / "openai-responses-proxy"
        projected = context.create(executable=str(executable), port=8808)

        assert projected.executable == str(executable)
        assert "python" not in projected.__dataclass_fields__
        assert "proxy_script" not in projected.__dataclass_fields__
        assert "watchdog_script" not in projected.__dataclass_fields__
        environment = runtime_spec.environment(runtime_spec.write(projected))
        assert "OPENAI_RESPONSES_PROXY_PROXY_PYTHON" not in environment
        assert "OPENAI_RESPONSES_PROXY_PROXY_SCRIPT" not in environment

    def test_runtime_spec_rejects_schema_location_and_setting_drift(self, tmp_path, subtests):
        install_dir = tmp_path / "payload"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(install_dir / "bin" / "openai-responses-proxy"),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "state"),
        )
        target = runtime_spec.write(projected)
        valid = json.loads(target.read_text(encoding="utf-8"))
        invalid = (
            ({**valid, "parallel_setting": True}, "schema is unsupported"),
            (
                {**valid, "install_dir": str(tmp_path / "other")},
                "outside the installed payload layout",
            ),
            ({**valid, "port": 0}, "must be an integer in 1..65535"),
        )

        for payload, message in invalid:
            with subtests.test(message=message):
                target.write_bytes(digest.canonical_json(payload))
                with pytest.raises(errors.InstallError, match=message):
                    runtime_spec.environment(target)

    def test_runtime_activation_replaces_only_product_settings(self, tmp_path, *, mocker):
        install_dir = tmp_path / "payload"
        executable = install_dir / "bin" / "openai-responses-proxy"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"native")
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(executable),
            command=str(tmp_path / "bin" / "openai-responses-proxy"),
            log_dir=str(tmp_path / "state"),
            port=8808,
        )
        expected = runtime_spec.environment(runtime_spec.write(projected))
        inherited = dict.fromkeys(config.RUNTIME_ENVIRONMENT, "stale")
        inherited["UNRELATED_SETTING"] = "preserved"
        mocker.patch.dict(os.environ, inherited, clear=True)

        runtime_spec.activate(executable)

        assert {name: os.environ[name] for name in config.RUNTIME_ENVIRONMENT} == expected
        assert os.environ["UNRELATED_SETTING"] == "preserved"

    def test_initial_body_budgets_persist_across_later_contexts(self, tmp_path, *, mocker):
        mocker.patch.object(config, "data_dir", return_value=str(tmp_path / "payload"))
        mocker.patch.object(config, "state_dir", return_value=str(tmp_path / "state"))
        mocker.patch.dict(
            os.environ,
            {
                "OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES": "16384",
                "OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT": "2.5",
                "OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "1.5",
                "OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES": "8192",
            },
            clear=True,
        )
        initial = context.create()
        target = runtime_spec.write(initial)
        original = target.read_bytes()
        environment = runtime_spec.environment(target)

        assert environment["OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES"] == "16384"
        assert environment["OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT"] == "2.5"
        assert environment.get("OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT") == "1.5"
        assert environment.get("OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES") == "8192"
        mocker.patch.dict(
            os.environ,
            {
                "OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES": "32768",
                "OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT": "10",
                "OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT": "10",
                "OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES": "16384",
            },
        )
        successor = context.create()
        assert successor.request_body_max_bytes == 16384
        assert successor.request_body_timeout == 2.5
        assert successor.catalog_timeout == 1.5
        assert successor.catalog_max_bytes == 8192
        assert runtime_spec.write(successor).read_bytes() == original

    def test_retained_carrier_gets_body_defaults_without_rewriting_it(self, tmp_path):
        ctx = context.RuntimeContext(
            install_dir=str(tmp_path / "payload"),
            executable=str(tmp_path / "payload" / "bin" / "openai-responses-proxy"),
            command=str(tmp_path / "command"),
            log_dir=str(tmp_path / "state"),
        )
        target = runtime_spec.write(ctx)
        retained = json.loads(target.read_bytes())
        retained.pop("request_body_max_bytes", None)
        retained.pop("request_body_timeout", None)
        retained.pop("catalog_timeout", None)
        retained.pop("catalog_max_bytes", None)
        original = digest.canonical_json(retained)
        target.write_bytes(original)

        environment = runtime_spec.environment(target)

        assert environment["OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES"] == "67108864"
        assert environment["OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT"] == "30.0"
        assert environment.get("OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT") == "30.0"
        assert environment.get("OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES") == "8388608"
        assert target.read_bytes() == original

    @pytest.mark.parametrize(
        ("name", "invalid"),
        [
            ("OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT", "0"),
            ("OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT", "NaN"),
            ("OPENAI_RESPONSES_PROXY_CATALOG_TIMEOUT", "301"),
            ("OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES", "4095"),
            ("OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES", "67108865"),
            ("OPENAI_RESPONSES_PROXY_CATALOG_MAX_BYTES", "1.5"),
        ],
    )
    def test_catalog_budget_selection_rejects_invalid_values(self, name, invalid):
        with pytest.raises(config.ConfigurationError):
            config.load({name: invalid})

    def test_runtime_activation_rejects_missing_carrier(self, tmp_path):
        executable = tmp_path / "payload" / "bin" / "openai-responses-proxy"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"native")

        with pytest.raises(errors.InstallError, match="unavailable or invalid"):
            runtime_spec.activate(executable)

    def test_context_rejects_invalid_cli_overrides(self, subtests, *, mocker):
        invalid: tuple[_RuntimeOverrides, ...] = (
            {"port": 0},
            {"port": 65536},
            {"proxy_log_max_bytes": 4095},
            {"proxy_log_backup_count": -1},
            {"watchdog_log_max_bytes": 64 * 1024 * 1024 + 1},
            {"watchdog_log_backup_count": 11},
        )
        mocker.patch.object(config, "home_dir", return_value=os.sep)
        mocker.patch.object(config, "data_dir", return_value="/payload")
        mocker.patch.object(config, "state_dir", return_value="/state")
        for values in invalid:
            with subtests.test(values=values), pytest.raises(errors.InstallError):
                context.create(executable="/portable/bin/openai-responses-proxy", **values)

    def test_service_projection_is_derived_from_one_runtime_contract(self, tmp_path):
        install_dir = tmp_path / "proxy"
        executable = install_dir / "bin" / "openai-responses-proxy"
        projected = context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(executable),
            command="/fixture/user-root/.local/bin/openai-responses-proxy",
            log_dir="/var/state/proxy",
            port=8808,
            upstream_timeout=45.0,
            catalog_timeout=0.25,
            catalog_max_bytes=8192,
        )
        environment = runtime_spec.environment(runtime_spec.write(projected))
        assert set(environment) == {
            config.HOME_ENV,
            config.STATE_HOME_ENV,
            config.PROXY_PORT_ENV,
            config.PROXY_LOG_ENV,
            config.WATCHDOG_LOG_ENV,
            config.PROXY_LOG_MAX_BYTES_ENV,
            config.PROXY_LOG_BACKUP_COUNT_ENV,
            config.WATCHDOG_LOG_MAX_BYTES_ENV,
            config.WATCHDOG_LOG_BACKUP_COUNT_ENV,
            config.UPSTREAM_TIMEOUT_ENV,
            config.UPSTREAM_READ_TIMEOUT_ENV,
            config.CATALOG_TIMEOUT_ENV,
            config.CATALOG_MAX_BYTES_ENV,
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES",
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT",
            config.UPSTREAM_BASE_URL_ENV,
            config.WATCHDOG_INTERVAL_ENV,
            config.WATCHDOG_MAX_BACKOFF_ENV,
            config.RESPONSE_FAILED_COMPACTION_BUDGET_ENV,
            config.RESPONSE_FAILED_MAX_STAGES_ENV,
        }
        assert environment[config.HOME_ENV] == str(install_dir)
        assert environment[config.STATE_HOME_ENV] == "/var/state/proxy"
        assert config.load(environment).listener == ("127.0.0.1", 8808)
        assert environment[config.UPSTREAM_TIMEOUT_ENV] == "45.0"
        settings = config.load(environment)
        assert settings.catalog_timeout == 0.25
        assert settings.catalog_max_bytes == 8192
