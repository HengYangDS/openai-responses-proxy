"""Declarative provider registry contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openai_responses_proxy.providers import registry

ROOT = Path(__file__).resolve().parents[2]


def _manifest(root: Path, text: str) -> Path:
    path = root / "openai_responses_proxy/providers/manifest.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class ProviderRegistryTests:
    def test_accepts_only_absolute_http_upstream_urls(self, subtests: pytest.Subtests) -> None:
        assert registry._base_url("test", "https://example.test/") == "https://example.test"
        assert (
            registry._base_url("test", "https://example.test:8443/v1")
            == "https://example.test:8443/v1"
        )
        assert registry._base_url("test", "http://127.0.0.1:8791/v1/") == "http://127.0.0.1:8791/v1"
        invalid = (
            None,
            "",
            "https://example.test/with space",
            "http://example.test",
            "https://user:secret@example.test",
            "https://example.test?route=other",
            "https://example.test#fragment",
            "https://example.test:bad",
            "not-a-url",
        )
        for value in invalid:
            with (
                subtests.test(value=value),
                pytest.raises(
                    ValueError,
                    match=r"^provider 'test' base_url must be an absolute HTTP\(S\) URL$",
                ),
            ):
                registry._base_url("test", value)

    def test_manifest_loader_rejects_each_closed_schema_boundary(
        self, subtests: pytest.Subtests
    ) -> None:
        documents = (
            ("not toml = [", "cannot load provider manifest"),
            (
                "version = 2\n[providers.dmxapi]\nbase_url = 'https://x.test'\n",
                "provider manifest version must be 1",
            ),
            ("version = 1\n", "provider manifest must define at least one provider"),
            (
                (
                    "version = 1\ndefault = 'dmxapi'\n"
                    "[providers.dmxapi]\nbase_url = 'https://x.test'\n"
                ),
                "provider manifest has no implicit default route",
            ),
            (
                "version = 1\n[providers.Bad]\nbase_url = 'https://x.test'\n",
                "provider names must be lowercase kebab-case slugs",
            ),
            (
                "version = 1\n[providers.x]\nbase_url = 'https://x.test'\nextra = true\n",
                "provider 'x' has unknown or invalid fields",
            ),
            (
                "version = 1\n[providers.x]\nbase_url = 'https://x.test'\npolicy = 'dmxapi'\n",
                "provider 'x' has unknown or invalid fields",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for document, message in documents:
                with subtests.test(document=document), pytest.raises(ValueError, match=message):
                    registry.load(_manifest(root, document))
            loaded = registry.load(
                _manifest(
                    root, "version = 1\n[providers.dmxapi]\nbase_url = 'https://x.test/v1/'\n"
                )
            )
        assert loaded.profiles["dmxapi"].base_url == "https://x.test/v1"
        with pytest.raises(ValueError, match="cannot load provider manifest"):
            registry.load(Path("does-not-exist.toml"))

    def test_ordinary_provider_requires_only_one_manifest_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = registry.load(
                _manifest(
                    Path(directory),
                    "version = 1\n"
                    "[providers.new-gateway]\nbase_url = 'https://gateway.example/v1'\n",
                )
            )
        assert tuple(loaded.profiles) == ("new-gateway",)
        assert loaded.profiles["new-gateway"].base_url == "https://gateway.example/v1"

    def test_shipped_manifest_routes_suheai_as_an_ordinary_provider(self) -> None:
        shipped = registry.load()
        profile = shipped.profiles["suheai"]
        assert profile.base_url == "https://suheai.cn/v1"
        assert profile.agent_message_delivery == "native"
        assert shipped.resolve("/suheai/v1/responses") == (
            profile,
            "responses",
            "https://suheai.cn/v1/responses",
        )
        assert shipped.resolve("/suheai/v1/models") == (
            profile,
            "models",
            "https://suheai.cn/v1/models",
        )

    def test_agent_delivery_policy_is_explicit_and_scoped_to_one_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = registry.load(
                _manifest(
                    Path(directory),
                    "version = 1\n"
                    "[providers.native]\nbase_url = 'https://native.example/v1'\n"
                    "[providers.readable]\nbase_url = 'https://readable.example/v1'\n"
                    "agent_message_delivery = 'plaintext'\n",
                )
            )
        assert loaded.profiles["native"].agent_message_delivery == "native"
        assert loaded.profiles["readable"].agent_message_delivery == "plaintext"
        shipped = registry.load()
        assert shipped.profiles["ucloud"].agent_message_delivery == "plaintext"
        assert shipped.profiles["dmxapi"].agent_message_delivery == "native"

    @pytest.mark.parametrize("value", ["'automatic'", "false"])
    def test_unknown_agent_delivery_policy_cannot_start_a_route(self, value: str) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            pytest.raises(ValueError, match="invalid agent message delivery policy"),
        ):
            registry.load(
                _manifest(
                    Path(directory),
                    "version = 1\n[providers.gateway]\n"
                    "base_url = 'https://gateway.example/v1'\n"
                    f"agent_message_delivery = {value}\n",
                )
            )

    def test_registry_rejects_ambiguous_or_malformed_request_targets(
        self, subtests: pytest.Subtests
    ) -> None:
        routes = registry.Registry({"gateway": registry.Profile("gateway", "https://x.test")})
        for path in ("http://[invalid", "https://x.test/v1/responses", "/v2/responses"):
            with subtests.test(path=path):
                assert routes.resolve(path) is None

    def test_registry_resolves_only_declared_provider_routes(
        self, subtests: pytest.Subtests
    ) -> None:
        routes = registry.Registry(
            {
                "alpha": registry.Profile("alpha", "https://alpha.example/v1"),
                "beta": registry.Profile("beta", "https://beta.example/v1"),
            },
        )
        assert routes.resolve("/alpha/v1/responses") == (
            routes.profiles["alpha"],
            "responses",
            "https://alpha.example/v1/responses",
        )
        assert routes.resolve("/beta/v1/responses?include=usage") == (
            routes.profiles["beta"],
            "responses",
            "https://beta.example/v1/responses?include=usage",
        )
        assert routes.resolve("/beta/v1/models?limit=10") == (
            routes.profiles["beta"],
            "models",
            "https://beta.example/v1/models?limit=10",
        )
        for path in (
            "/v1/responses",
            "/unknown/v1/responses",
            "/alpha/other",
            "/beta/v2/responses",
            "/beta/v1/responsesx",
            "/beta/v1//responses",
            "/beta/v1/../admin",
            "/beta/v1/%2e%2e/admin",
            "/beta/v1/responses%2fextra",
            "/beta/v1/responses#fragment",
            "//beta/v1/responses",
            "https://beta.example/v1/responses",
            "http://[invalid",
        ):
            with subtests.test(path=path):
                assert routes.resolve(path) is None

    def test_explicit_upstream_is_an_ordinary_isolated_responses_route(self) -> None:
        released = registry.Registry(
            {"gateway": registry.Profile("gateway", "https://gateway.example/v1")}
        )
        configured = released.with_upstream("https://api.openai.com/v1/")

        assert set(released.profiles) == {"gateway"}
        assert configured.resolve("/upstream/v1/responses") == (
            configured.profiles["upstream"],
            "responses",
            "https://api.openai.com/v1/responses",
        )
        assert configured.profiles["upstream"].agent_message_delivery == "native"
        assert configured.resolve("/gateway/v1/models") == released.resolve("/gateway/v1/models")
        assert released.with_upstream("") is released
