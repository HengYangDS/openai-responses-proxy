"""Portable runtime roots, settings and validation contracts."""

from __future__ import annotations

import ntpath
from collections.abc import Mapping
from typing import cast

import pytest

from openai_responses_proxy.runtime import config as runtime_config


class TestRuntimeConfiguration:
    def test_home_dir_expands_the_runtime_user_home(self, *, mocker):
        mocker.patch.object(runtime_config.os.path, "expanduser", return_value="/portable/home")
        assert runtime_config.home_dir() == "/portable/home"

    def test_product_roots_are_portable_and_explicitly_overridable(self, subtests, *, mocker):
        cases = (
            (
                {"OPENAI_RESPONSES_PROXY_HOME": "~/payload"},
                {"os.name": "posix", "sys.platform": "linux"},
                ("payload", ".local/state/openai-responses-proxy"),
            ),
            (
                {"OPENAI_RESPONSES_PROXY_STATE_HOME": "~/state"},
                {"os.name": "posix", "sys.platform": "darwin"},
                ("Library/Application Support/openai-responses-proxy", "state"),
            ),
            (
                {"LOCALAPPDATA": "/portable/local"},
                {"os.name": "nt", "sys.platform": "win32"},
                (
                    "/portable/local/openai-responses-proxy",
                    "/portable/local/openai-responses-proxy-state",
                ),
            ),
            (
                {
                    "XDG_DATA_HOME": "/portable/data",
                    "XDG_STATE_HOME": "/portable/state",
                },
                {"os.name": "posix", "sys.platform": "linux"},
                (
                    "/portable/data/openai-responses-proxy",
                    "/portable/state/openai-responses-proxy",
                ),
            ),
        )
        for environment, platform, expected in cases:
            mocker.patch.dict(runtime_config.os.environ, environment, clear=True)
            mocker.patch.object(runtime_config.os, "name", platform["os.name"])
            mocker.patch.object(runtime_config.sys, "platform", platform["sys.platform"])
            mocker.patch.object(runtime_config, "home_dir", return_value="/home/tester")
            with subtests.test(platform=platform):
                assert runtime_config.data_dir().endswith(expected[0])
                assert runtime_config.state_dir().endswith(expected[1])

    def test_macos_state_root_is_not_lost_in_platform_matrix(self, *, mocker):
        """Keep the native macOS path branch covered on every CI host."""
        mocker.patch.dict(runtime_config.os.environ, {}, clear=True)
        mocker.patch.object(runtime_config.os, "name", "posix")
        mocker.patch.object(runtime_config.sys, "platform", "darwin")
        home = runtime_config.path_join("/", "portable", "user")
        mocker.patch.object(runtime_config, "home_dir", return_value=home)

        assert runtime_config.state_dir() == runtime_config.path_join(
            home, "Library", "Logs", "openai-responses-proxy"
        )

    def test_runtime_configuration_is_loopback_only_and_rejects_invalid_ports(self, subtests):
        assert runtime_config.listener_host() == "127.0.0.1"
        for value in ("0", "65536", "not-a-port"):
            with (
                subtests.test(value=value),
                pytest.raises(runtime_config.ConfigurationError),
            ):
                runtime_config.listener_port({runtime_config.PROXY_PORT_ENV: value})

    def test_runtime_log_defaults_share_the_portable_state_owner(self, *, mocker):
        mocker.patch.dict(runtime_config.os.environ, {}, clear=True)
        mocker.patch.object(runtime_config, "state_dir", return_value="/portable/state")
        assert runtime_config.proxy_log_path() == "/portable/state/proxy.log"
        assert runtime_config.watchdog_log_path() == "/portable/state/watchdog.log"

    def test_runtime_path_overrides_expand_against_the_supplied_home(self):
        environment = {
            "HOME": "/portable/home",
            runtime_config.HOME_ENV: "~/payload",
            runtime_config.STATE_HOME_ENV: "~/state",
        }
        assert runtime_config.data_dir(environment) == "/portable/home/payload"
        assert runtime_config.state_dir(environment) == "/portable/home/state"

    def test_posix_overrides_are_not_reinterpreted_by_a_windows_host(self, *, mocker):
        environment = {
            "HOME": "/portable/home",
            runtime_config.HOME_ENV: "~/payload",
            runtime_config.STATE_HOME_ENV: "/portable/state",
        }
        mocker.patch.object(runtime_config.os, "path", ntpath)
        assert runtime_config.data_dir(environment) == "/portable/home/payload"
        assert runtime_config.state_dir(environment) == "/portable/state"

    def test_runtime_settings_expose_only_the_owned_configuration_model(self):
        assert set(runtime_config.Settings.__dataclass_fields__) == {
            "host",
            "port",
            "proxy_log",
            "watchdog_log",
            "upstream_timeout",
            "upstream_read_timeout",
            "catalog_timeout",
            "catalog_max_bytes",
            "request_body_max_bytes",
            "request_body_timeout",
            "watchdog_interval",
            "watchdog_max_backoff",
            "response_failed_compaction_budget",
            "response_failed_max_stages",
        }
        settings = runtime_config.load({})
        assert settings.listener == (
            runtime_config.listener_host(),
            runtime_config.DEFAULT_PORT,
        )

    def test_runtime_settings_have_one_owner_and_strict_validation(self, subtests):
        environment = {
            runtime_config.PROXY_PORT_ENV: "8801",
            runtime_config.PROXY_LOG_MAX_BYTES_ENV: "8192",
            runtime_config.PROXY_LOG_BACKUP_COUNT_ENV: "4",
            runtime_config.WATCHDOG_LOG_MAX_BYTES_ENV: "12288",
            runtime_config.WATCHDOG_LOG_BACKUP_COUNT_ENV: "5",
            runtime_config.UPSTREAM_TIMEOUT_ENV: "30",
            runtime_config.UPSTREAM_READ_TIMEOUT_ENV: "15.5",
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES": "16384",
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT": "2.5",
            runtime_config.WATCHDOG_INTERVAL_ENV: "3",
            runtime_config.WATCHDOG_MAX_BACKOFF_ENV: "12",
            runtime_config.RESPONSE_FAILED_COMPACTION_BUDGET_ENV: "65536",
            runtime_config.RESPONSE_FAILED_MAX_STAGES_ENV: "2",
        }
        settings = runtime_config.load(environment)
        assert settings.listener == ("127.0.0.1", 8801)
        assert settings.proxy_log.max_bytes == 8192
        assert settings.proxy_log.backup_count == 4
        assert settings.watchdog_log.max_bytes == 12288
        assert settings.watchdog_log.backup_count == 5
        assert settings.upstream_timeout == 30
        assert settings.upstream_read_timeout == 15.5
        assert settings.request_body_max_bytes == 16384
        assert settings.request_body_timeout == 2.5
        assert settings.watchdog_interval == 3
        assert settings.watchdog_max_backoff == 12
        assert settings.response_failed_compaction_budget == 65536
        assert settings.response_failed_max_stages == 2

        invalid = (
            (runtime_config.PROXY_PORT_ENV, True),
            (runtime_config.PROXY_LOG_MAX_BYTES_ENV, "4095"),
            (runtime_config.PROXY_LOG_BACKUP_COUNT_ENV, "11"),
            (runtime_config.UPSTREAM_TIMEOUT_ENV, "0"),
            (runtime_config.UPSTREAM_READ_TIMEOUT_ENV, "infinity"),
            ("OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES", "4095"),
            ("OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES", "268435457"),
            ("OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT", "0"),
            ("OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT", "nan"),
            (runtime_config.WATCHDOG_INTERVAL_ENV, "-1"),
            (runtime_config.WATCHDOG_MAX_BACKOFF_ENV, "not-a-number"),
            (runtime_config.WATCHDOG_MAX_BACKOFF_ENV, False),
            (runtime_config.RESPONSE_FAILED_COMPACTION_BUDGET_ENV, "4095"),
            (runtime_config.RESPONSE_FAILED_MAX_STAGES_ENV, "9"),
        )
        for name, value in invalid:
            with (
                subtests.test(name=name, value=value),
                pytest.raises(runtime_config.ConfigurationError),
            ):
                runtime_config.load(cast("Mapping[str, str]", {name: value}))
