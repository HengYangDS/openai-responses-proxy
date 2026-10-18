"""Repository governance, release-metadata, and privacy-surface contracts."""

from __future__ import annotations

import importlib.util
import ntpath
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.runtime import config as runtime_config
from tools.git_environment import isolated_config_environment

ROOT = Path(__file__).resolve().parents[2]


class TestInstallationInputValidation:
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

    def test_service_projection_is_derived_from_one_runtime_contract(self, tmp_path):
        install_dir = tmp_path / "proxy"
        executable = install_dir / "bin" / "openai-responses-proxy"
        context = runtime_context.RuntimeContext(
            install_dir=str(install_dir),
            executable=str(executable),
            command="/fixture/user-root/.local/bin/openai-responses-proxy",
            log_dir="/var/state/proxy",
            port=8808,
            upstream_timeout=45.0,
        )
        environment = runtime_spec.environment(runtime_spec.write(context))
        assert set(environment) == {
            runtime_config.HOME_ENV,
            runtime_config.STATE_HOME_ENV,
            runtime_config.PROXY_PORT_ENV,
            runtime_config.PROXY_LOG_ENV,
            runtime_config.WATCHDOG_LOG_ENV,
            runtime_config.PROXY_LOG_MAX_BYTES_ENV,
            runtime_config.PROXY_LOG_BACKUP_COUNT_ENV,
            runtime_config.WATCHDOG_LOG_MAX_BYTES_ENV,
            runtime_config.WATCHDOG_LOG_BACKUP_COUNT_ENV,
            runtime_config.UPSTREAM_TIMEOUT_ENV,
            runtime_config.UPSTREAM_READ_TIMEOUT_ENV,
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES",
            "OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT",
            runtime_config.UPSTREAM_BASE_URL_ENV,
            runtime_config.WATCHDOG_INTERVAL_ENV,
            runtime_config.WATCHDOG_MAX_BACKOFF_ENV,
            runtime_config.RESPONSE_FAILED_COMPACTION_BUDGET_ENV,
            runtime_config.RESPONSE_FAILED_MAX_STAGES_ENV,
        }
        assert environment[runtime_config.HOME_ENV] == str(install_dir)
        assert environment[runtime_config.STATE_HOME_ENV] == "/var/state/proxy"
        assert runtime_config.load(environment).listener == ("127.0.0.1", 8808)
        assert environment[runtime_config.UPSTREAM_TIMEOUT_ENV] == "45.0"

    def test_runtime_settings_expose_only_the_owned_configuration_model(self):
        assert set(runtime_config.Settings.__dataclass_fields__) == {
            "host",
            "port",
            "proxy_log",
            "watchdog_log",
            "upstream_timeout",
            "upstream_read_timeout",
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


class TestGovernanceMetadata:
    def test_product_module_names_do_not_shadow_the_standard_library(self):
        collisions = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src" / "openai_responses_proxy").rglob("*.py")
            if path.stem != "__init__" and path.stem in sys.stdlib_module_names
        )
        assert collisions == []

    def test_listener_port_literals_have_one_production_owner(self):
        production = ROOT / "src" / "openai_responses_proxy"
        owner = production / "runtime" / "config.py"
        owner_text = owner.read_text(encoding="utf-8")
        assert owner_text.count("8792") == 1
        assert "8791" not in owner_text
        copied = []
        for source in production.rglob("*.py"):
            if source == owner:
                continue
            text = source.read_text(encoding="utf-8")
            if "8791" in text or "8792" in text:
                copied.append(source.relative_to(ROOT).as_posix())
        assert copied == []

    def test_ignore_policy_excludes_generated_state_not_authored_configuration(self, tmp_path):
        environment = isolated_config_environment()
        subprocess.run(["git", "init", "-q", str(tmp_path)], env=environment, check=True)
        (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        generated = (
            ".cache/pytest/cache",
            ".nox/quality/tmp/wheel.whl",
            ".venv/bin/python",
            "node_modules/package/index.js",
            "build/verification/check.json",
            ".coverage",
            ".release-assets/linux-x86_64/archive.tar.gz",
            ".ruff_cache/index",
            "src/package/__pycache__/module.pyc",
            ".serena/project.yml",
            ".code-memory/index",
            ".codebase-memory/graph.db.zst",
        )
        authored = ("tests/fixtures/config.toml", "docs/config.toml", ".config/policy.toml")
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", "--stdin", "-z"],
            input="\0".join((*generated, *authored)).encode() + b"\0",
            env=environment,
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0
        assert set(result.stdout.decode().rstrip("\0").split("\0")) == set(generated)

    def test_forge_publication_has_no_implicit_actor_or_trust_source(self):
        source = (ROOT / "tools" / "release" / "tag_signature.py").read_text(encoding="utf-8")
        assert "packaging/release" not in source
        assert "/Users/" not in source
        assert "$HOME/.ssh" not in source
        assert "anchor" in source

    def test_semantic_packages_are_declarative_and_facade_free(self):
        package_root = ROOT / "src/openai_responses_proxy"
        packages = {
            path.name
            for path in package_root.iterdir()
            if path.is_dir() and not path.name.startswith("__")
        }
        assert packages == {
            "cli",
            "lifecycle",
            "protocol",
            "providers",
            "relay",
            "runtime",
            "serving",
        }
        for package in (package_root / name for name in sorted(packages)):
            source = (ROOT / package / "__init__.py").read_text(encoding="utf-8")
            assert "import " not in source, package

    def test_runtime_context_has_one_semantic_owner(self):
        assert (ROOT / "src/openai_responses_proxy/lifecycle/context.py").is_file()

    def test_provider_specific_wire_policies_have_a_semantic_owner(self):
        assert (ROOT / "src/openai_responses_proxy/providers/policies/dmxapi.py").is_file()

    def test_publication_has_importable_command_and_peer_owners(self):
        for owner in ("__main__", "verification", "github.publish", "gitlab.publish"):
            assert importlib.util.find_spec(f"tools.release.publication.{owner}") is not None

    def test_governance_hooks_are_installed_runtime_state_not_product_source(self):
        assert not (ROOT / ".githooks").exists()

    def test_lifecycle_never_reads_or_prescribes_client_state(self):
        text = "\n".join(
            Path(ROOT, relative).read_text(encoding="utf-8").lower()
            for relative in (
                "src/openai_responses_proxy/lifecycle/install.py",
                "src/openai_responses_proxy/lifecycle/uninstall.py",
            )
        )
        assert "fully " + "quit & reopen" not in text
        assert "start a " + "new codex thread" not in text
        assert "conversation" not in text
        assert "config.toml" not in text


class TestReleaseMetadata:
    def test_control_and_data_planes_keep_explicit_privacy_boundaries(self, subtests):
        cases = (
            (
                ("src/openai_responses_proxy/lifecycle/control.py",),
                ("def status",),
                ("AIGW", "ChatGPT", "JetBrains"),
            ),
            (
                ("src/openai_responses_proxy/serving/entrypoint.py",),
                (),
                (
                    "OPENAI_RESPONSES_PROXY_DUMP_BODIES",
                    "OPENAI_RESPONSES_PROXY_DUMP_HEADERS",
                    "reject-",
                ),
            ),
            (
                ("src/openai_responses_proxy/runtime/config.py",),
                (
                    'product_identity.environment_name("PROXY_LOG_MAX_BYTES")',
                    'product_identity.environment_name("PROXY_LOG_BACKUP_COUNT")',
                    'product_identity.environment_name("WATCHDOG_LOG_MAX_BYTES")',
                    'product_identity.environment_name("WATCHDOG_LOG_BACKUP_COUNT")',
                ),
                ("OPENAI_RESPONSES_PROXY_PROXY_LOG_MAX_BYTES",),
            ),
        )
        for paths, required, forbidden in cases:
            source = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in paths)
            with subtests.test(paths=paths):
                for value in required:
                    assert value in source
                for value in forbidden:
                    assert value not in source

    def test_mit_license_is_present(self):
        license_text = Path(ROOT, "LICENSE").read_text(encoding="utf-8")
        assert license_text.startswith("MIT License\n")
