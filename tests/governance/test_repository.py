"""Repository governance, release-metadata, and privacy-surface contracts."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from tools.git_environment import isolated_config_environment

ROOT = Path(__file__).resolve().parents[2]


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
