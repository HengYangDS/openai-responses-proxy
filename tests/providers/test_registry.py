"""Declarative provider registry and closed wire-policy extension contracts."""

from __future__ import annotations

import importlib.machinery
import inspect
import sys
import tempfile
from collections.abc import Generator
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from openai_responses_proxy.providers import registry

ROOT = Path(__file__).resolve().parents[2]


def _policy_source(version: str) -> str:
    return (
        "EXHAUSTED_CODE = 'retry_exhausted'\n"
        "EXHAUSTED_MESSAGE = 'retry later'\n"
        "FAILURE_CACHE_CAPACITY = 1\n"
        "FAILURE_COOLDOWN_SECONDS = 1\n"
        f"POLICY_VERSION = {version!r}\n"
        "def is_retryable_failure(status, payload): return False\n"
        f"def request_fingerprint(raw): return {version!r}\n"
    )


def _manifest(root: Path, text: str) -> Path:
    path = root / "openai_responses_proxy/providers/manifest.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@contextmanager
def _policy_package(root: Path, name: str, modules: Mapping[str, str], *, mocker) -> Generator[str]:
    package = root / name
    policies = package / "policies"
    policies.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (policies / "__init__.py").write_text("", encoding="utf-8")
    for module, source in modules.items():
        (policies / f"{module}.py").write_text(source, encoding="utf-8")
    prefix = f"{name}.policies"
    package_module = ModuleType(name)
    package_module.__path__ = [str(package)]
    policy_package = ModuleType(prefix)
    policy_package.__path__ = [str(policies)]
    mocker.patch.object(registry, "POLICY_PACKAGE", prefix)
    mocker.patch.dict(sys.modules, {name: package_module, prefix: policy_package})
    try:
        yield prefix
    finally:
        for module in tuple(sys.modules):
            if module == name or module.startswith(f"{name}."):
                sys.modules.pop(module, None)


class ProviderRegistryTests:
    def test_accepts_only_absolute_http_upstream_urls(self, subtests) -> None:
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

    def test_manifest_loader_rejects_each_closed_schema_boundary(self, subtests) -> None:
        documents = (
            ("not toml = [", "cannot load provider manifest"),
            (
                "version = 2\n[providers.dmxapi]\nbase_url = 'https://x.test'\n",
                "provider manifest version must be 1",
            ),
            ("version = 1\n", "provider manifest must define at least one provider"),
            (
                "version = 1\ndefault = 'dmxapi'\n"
                "[providers.dmxapi]\nbase_url = 'https://x.test'\n",
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
                "version = 1\n[providers.x]\nbase_url = 'https://x.test'\npolicy = 'unknown'\n",
                "provider policy 'unknown' is unavailable",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for document, message in documents:
                with subtests.test(document=document), pytest.raises(ValueError, match=message):
                    registry.load(_manifest(root, document))
            loaded = registry.load(
                _manifest(
                    root,
                    "version = 1\n"
                    "[providers.dmxapi]\nbase_url = 'https://x.test/v1/'\npolicy = 'dmxapi'\n",
                )
            )
        assert loaded.profiles["dmxapi"].wire_policy is not None
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
        assert loaded.profiles["new-gateway"].wire_policy is None

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

    def test_special_policy_is_one_module_plus_one_manifest_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = registry.load(
                _manifest(
                    Path(directory),
                    "version = 1\n"
                    "[providers.gateway]\nbase_url = 'https://gateway.example/v1'\n"
                    "policy = 'dmxapi'\n",
                )
            )
        policy = loaded.profiles["gateway"].wire_policy
        assert policy is not None
        assert policy is not None
        assert policy.POLICY_VERSION == "empty-response-retry-v1"
        assert not hasattr(policy, "build_fallback")
        assert not hasattr(policy, "recover_dialogue")
        assert registry.policy_module_names(loaded) == (
            "openai_responses_proxy.providers.policies.dmxapi",
        )

    def test_policy_loader_rejects_path_missing_and_incomplete_extensions(
        self, subtests, *, mocker
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _policy_package(
                root,
                "invalid_policy_fixture",
                {"incomplete": "POLICY_VERSION = 'v1'\n"},
                mocker=mocker,
            ):
                documents = (
                    ("policy = '../escape'\n", "references an unknown policy"),
                    ("policy = 'missing'\n", "provider policy 'missing' is unavailable"),
                    (
                        "policy = 'incomplete'\n",
                        "provider policy 'incomplete' does not implement the wire-policy contract",
                    ),
                )
                for declaration, message in documents:
                    with (
                        subtests.test(declaration=declaration),
                        pytest.raises(ValueError, match=message),
                    ):
                        registry.load(
                            _manifest(
                                root,
                                "version = 1\n[providers.gateway]\n"
                                "base_url = 'https://gateway.example/v1'\n" + declaration,
                            )
                        )

    def test_policy_module_identity_is_package_scoped(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _policy_package(
                root,
                "first_policy_fixture",
                {"shared": _policy_source("first")},
                mocker=mocker,
            ):
                first = registry.load(
                    _manifest(
                        root,
                        "version = 1\n[providers.gateway]\n"
                        "base_url = 'https://gateway.example/v1'\npolicy = 'shared'\n",
                    )
                )
            with _policy_package(
                root,
                "second_policy_fixture",
                {"shared": _policy_source("second")},
                mocker=mocker,
            ):
                second = registry.load(root / "openai_responses_proxy/providers/manifest.toml")
        first_policy = first.profiles["gateway"].wire_policy
        second_policy = second.profiles["gateway"].wire_policy
        assert first_policy is not None
        assert second_policy is not None
        assert (first_policy.POLICY_VERSION, second_policy.POLICY_VERSION) == (
            "first",
            "second",
        )

    def test_failed_manifest_load_leaves_no_policy_module_residue(
        self, subtests, *, mocker
    ) -> None:
        cases = (
            (
                "later provider",
                "version = 1\n"
                "[providers.first]\nbase_url = 'https://first.example/v1'\npolicy = 'first'\n"
                "[providers.second]\nbase_url = 'https://second.example/v1'\npolicy = 'missing'\n",
                "provider policy 'missing' is unavailable",
            ),
            (
                "explicit default",
                "version = 1\ndefault = 'first'\n"
                "[providers.first]\nbase_url = 'https://first.example/v1'\npolicy = 'first'\n",
                "provider manifest has no implicit default route",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _policy_package(
                root,
                "atomic_policy_fixture",
                {"first": _policy_source("first")},
                mocker=mocker,
            ) as prefix:
                module_name = f"{prefix}.first"
                for label, document, message in cases:
                    with subtests.test(label=label), pytest.raises(ValueError, match=message):
                        registry.load(_manifest(root, document))
                    assert module_name not in sys.modules

    def test_loaded_policy_module_must_live_inside_its_declared_package(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.py"
            outside.write_text("outside\n", encoding="utf-8")
            with _policy_package(root, "boundary_policy_fixture", {}, mocker=mocker) as prefix:
                fake = ModuleType(f"{prefix}.escape")
                fake.__file__ = str(outside)
                fake.__dict__.update(
                    EXHAUSTED_CODE="retry_exhausted",
                    EXHAUSTED_MESSAGE="retry later",
                    FAILURE_CACHE_CAPACITY=1,
                    FAILURE_COOLDOWN_SECONDS=1,
                    POLICY_VERSION="escape",
                    is_retryable_failure=lambda _status, _payload: False,
                    request_fingerprint=lambda _raw: "escape",
                )
                mocker.patch.dict(sys.modules, {fake.__name__: fake})
                with pytest.raises(ValueError, match="outside its policy package"):
                    registry.load(
                        _manifest(
                            root,
                            "version = 1\n[providers.gateway]\n"
                            "base_url = 'https://gateway.example/v1'\npolicy = 'escape'\n",
                        )
                    )

    def test_frozen_policy_module_uses_import_identity_without_a_filesystem_path(
        self, *, mocker
    ) -> None:
        fake = ModuleType("openai_responses_proxy.providers.policies.frozen")
        fake.__file__ = None
        fake.__package__ = "openai_responses_proxy.providers.policies"
        fake.__spec__ = importlib.machinery.ModuleSpec(fake.__name__, loader=None)
        fake.__dict__.update(
            EXHAUSTED_CODE="retry_exhausted",
            EXHAUSTED_MESSAGE="retry later",
            FAILURE_CACHE_CAPACITY=1,
            FAILURE_COOLDOWN_SECONDS=1,
            POLICY_VERSION="frozen",
            is_retryable_failure=lambda _status, _payload: False,
            request_fingerprint=lambda _raw: "frozen",
        )
        manifest = (
            "version = 1\n[providers.gateway]\n"
            "base_url = 'https://gateway.example/v1'\npolicy = 'frozen'\n"
        )
        mocker.patch.dict(sys.modules, {fake.__name__: fake})
        mocker.patch.object(registry.sys, "frozen", True, create=True)
        with tempfile.TemporaryDirectory() as directory:
            loaded = registry.load(_manifest(Path(directory), manifest))
        assert loaded.profiles["gateway"].wire_policy is fake

    def test_public_loader_does_not_expose_an_arbitrary_policy_package(self) -> None:
        assert tuple(inspect.signature(registry.load).parameters) == ("path",)

    def test_policy_import_failure_is_a_clean_manifest_error(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _policy_package(
                root,
                "broken_policy_fixture",
                {"broken": "raise RuntimeError('module initialization failed')\n"},
                mocker=mocker,
            ) as prefix:
                with pytest.raises(ValueError, match="provider policy 'broken' is unavailable"):
                    registry.load(
                        _manifest(
                            root,
                            "version = 1\n[providers.gateway]\n"
                            "base_url = 'https://gateway.example/v1'\npolicy = 'broken'\n",
                        )
                    )
                assert f"{prefix}.broken" not in sys.modules

    def test_registry_rejects_ambiguous_or_malformed_request_targets(self, subtests) -> None:
        routes = registry.Registry({"gateway": registry.Profile("gateway", "https://x.test")})
        for path in ("http://[invalid", "https://x.test/v1/responses", "/v2/responses"):
            with subtests.test(path=path):
                assert routes.resolve(path) is None

    def test_registry_resolves_only_declared_provider_routes(self, subtests) -> None:
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
        assert configured.profiles["upstream"].wire_policy is None
        assert configured.resolve("/gateway/v1/models") == released.resolve("/gateway/v1/models")
        assert released.with_upstream("") is released
