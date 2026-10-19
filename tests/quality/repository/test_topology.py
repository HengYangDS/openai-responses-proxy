"""Repository topology, package contracts and semantic ownership."""

from __future__ import annotations

import ast
import importlib.util
import tempfile
import tomllib
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.quality.repository.topology import architecture_gaps


def test_release_artifacts_have_one_construction_owner() -> None:
    package = importlib.util.find_spec("tools.release.artifact")
    assert package is not None
    assert package.submodule_search_locations is not None
    for concern in ("bundle", "format", "assembly", "signing", "__main__"):
        owner = importlib.util.find_spec(f"tools.release.artifact.{concern}")
        assert owner is not None


def test_repository_checks_share_one_semantic_package() -> None:
    """Repository-specific topology and naming belong to one audit package."""
    package = importlib.util.find_spec("tools.quality.repository")
    assert package is not None
    assert package.submodule_search_locations is not None
    for concern in ("topology", "names"):
        owner = importlib.util.find_spec(f"tools.quality.repository.{concern}")
        assert owner is not None


def test_current_product_architecture_is_acyclic_and_directional() -> None:
    assert architecture_gaps(ROOT) == []


def test_cli_is_the_only_production_command_composition_root() -> None:
    package = ROOT / "src/openai_responses_proxy"
    argparse_owners = []
    module_entrypoints = []
    shebangs = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        if source.startswith("#!"):
            shebangs.append(relative)
        if any(
            (isinstance(node, ast.Import) and any(alias.name == "argparse" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "argparse")
            for node in tree.body
        ):
            argparse_owners.append(relative)
        if any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and ast.unparse(node.test) == "__name__ == '__main__'"
            for node in tree.body
        ):
            module_entrypoints.append(relative)

    assert argparse_owners == []
    assert module_entrypoints == []
    assert shebangs == []
    assert (
        (package / "cli/__main__.py")
        .read_text(encoding="utf-8")
        .endswith("raise SystemExit(main())\n")
    )


def test_lifecycle_tests_follow_lifecycle_ownership() -> None:
    tests = ROOT / "tests"
    assert [
        name for name in ("deployment", "payload", "supervision") if (tests / name).exists()
    ] == []
    assert (tests / "lifecycle/fixtures.py").is_file()
    assert all(
        (tests / f"lifecycle/supervision/process/test_{owner}.py").is_file()
        for owner in ("identity", "termination")
    )
    assert (tests / "serving/test_identity.py").is_file()


def test_serving_tests_follow_runtime_and_deployment_ownership() -> None:
    tests = ROOT / "tests"
    assert [name for name in ("listener",) if (tests / name).exists()] == []
    assert (tests / "relay/fixtures.py").is_file()
    assert (tests / "runtime/test_process_environment.py").is_file()
    assert (tests / "serving/test_entrypoint.py").is_file()
    assert (tests / "serving/handoff/test_state_machine.py").is_file()
    assert all(
        (tests / f"lifecycle/deployment/handoff/test_{owner}.py").is_file()
        for owner in ("admission", "request", "drain", "recovery", "reload")
    )


def test_protocol_and_relay_tests_follow_terminal_ownership() -> None:
    tests = ROOT / "tests"
    owners = (
        "protocol/replay/test_projection.py",
        "protocol/replay/test_content.py",
        "protocol/replay/test_history.py",
        "protocol/replay/test_admission.py",
        "protocol/replay/test_items.py",
        "protocol/test_response.py",
        "protocol/recovery/test_input.py",
        "protocol/recovery/test_execution.py",
        "relay/empty_response/test_projection.py",
        "relay/empty_response/test_recovery.py",
        "relay/empty_response/test_cooldown.py",
        "relay/test_routes.py",
    )
    assert all((tests / owner).is_file() for owner in owners)


class TestArchitectureContracts:
    def test_architecture_gate_enforces_positive_topology_and_package_contracts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "control.py": "def main():\n    return 0\n",
                "src/openai_responses_proxy/__init__.py": "from .runtime import state\n",
                "src/openai_responses_proxy/common/value.py": "VALUE = 1\n",
                "src/openai_responses_proxy/serving/__init__.py": "",
                "src/openai_responses_proxy/extra/state.py": (
                    "from openai_responses_proxy.relay import downstream\n"
                ),
                "src/openai_responses_proxy/relay/downstream.py": (
                    "from openai_responses_proxy.runtime import state\n"
                ),
            }
            for relative, source in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")

            gaps = architecture_gaps(root)

        assert "architecture_root_implementation:control.py" in gaps
        assert "architecture_init_behavior:src/openai_responses_proxy/__init__.py" in gaps
        assert "architecture_undeclared_package:common" in gaps
        assert (
            "architecture_package_declaration_missing:src/openai_responses_proxy/serving/__init__.py"
            in gaps
        )
        assert "architecture_undeclared_package:extra" in gaps
        assert "architecture_disallowed_edge:relay->runtime" not in gaps
        assert "architecture_disallowed_edge:extra->relay" in gaps

    def test_architecture_gate_rejects_semantic_package_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "openai_responses_proxy"
            for owner, target in (("relay", "runtime"), ("runtime", "relay")):
                module = package / owner / "owner.py"
                module.parent.mkdir(parents=True)
                module.write_text(
                    f"from openai_responses_proxy.{target} import owner\n",
                    encoding="utf-8",
                )
            policy = {
                "owner": "quality",
                "risk_model": "cycles erase package ownership",
                "measurement": "parse semantic package imports",
                "false_positive_cost": "a legitimate cycle requires architecture review",
                "remediation": "restore one-way ownership",
                "review_condition": "reassess when the product topology changes",
                "source_roots": ["src/openai_responses_proxy"],
                "test_roots": ["tests"],
                "package_root": "src/openai_responses_proxy",
                "package_root_modules": [],
                "root_configuration_modules": [],
                "package_initializers": "ordinary-modules",
                "control_nesting": {"max_depth": 5},
                "product_concepts": {
                    "relay": "src/openai_responses_proxy/relay/owner.py",
                    "runtime": "src/openai_responses_proxy/runtime/owner.py",
                },
                "external_responsibilities": {"credentials": "client-control-plane"},
                "allowed_package_edges": {
                    "relay": ["runtime"],
                    "runtime": ["relay"],
                },
            }

            gaps = architecture_gaps(root, policy)

        assert gaps == ["architecture_cycle:relay,runtime"]

    def test_architecture_gate_admits_only_declared_runtime_imports_in_the_product(
        self, tmp_path: Path
    ) -> None:
        """Product code may import the standard library, itself and declared runtime dependencies."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\ndependencies = ["Rich-Text==1.0", "psutil==7.2.2"]\n',
            encoding="utf-8",
        )
        owner = tmp_path / "src/openai_responses_proxy/relay/owner.py"
        owner.parent.mkdir(parents=True)
        owner.write_text(
            "from __future__ import annotations\n"
            "import json\n"
            "import psutil\n"
            "import rich_text.console\n"
            "from . import sibling\n"
            "from openai_responses_proxy.relay import sibling\n"
            "import tools.quality\n"
            "from tests.fixtures import helper\n"
            "import ethos.cli, aigw\n"
            "from pytest import fixture\n",
            encoding="utf-8",
        )
        policy: dict[str, object] = {
            "owner": "quality",
            "risk_model": "a repository-only or foreign import couples the product to its workspace",
            "measurement": "parse product imports against declared runtime dependencies",
            "false_positive_cost": "a new runtime dependency is declared in project metadata",
            "remediation": "declare the dependency or move the coupling out of the product",
            "review_condition": "reassess when runtime dependencies change",
            "source_roots": ["src/openai_responses_proxy"],
            "test_roots": ["tests"],
            "package_root": "src/openai_responses_proxy",
            "package_root_modules": [],
            "root_configuration_modules": [],
            "package_initializers": "ordinary-modules",
            "control_nesting": {"max_depth": 5},
            "product_concepts": {"relay": "src/openai_responses_proxy/relay/owner.py"},
            "external_responsibilities": {"credentials": "client-control-plane"},
            "allowed_package_edges": {"relay": []},
        }

        assert architecture_gaps(tmp_path, policy) == [
            f"architecture_undeclared_import:{module}:src/openai_responses_proxy/relay/owner.py"
            for module in ("aigw", "ethos", "pytest", "tests", "tools")
        ]

    def test_architecture_gate_keeps_tool_domains_acyclic_and_independent_of_tests(
        self, tmp_path: Path
    ) -> None:
        """Repository tool domains form a one-way graph and never import the test tree."""
        for relative, source in (
            ("src/openai_responses_proxy/relay/owner.py", ""),
            ("tools/forge/tag.py", "from tools.release import identity\n"),
            (
                "tools/release/publish.py",
                "from tools.forge import tag\nimport tools.quality.audit\n",
            ),
            ("tools/quality/audit.py", "from tests.fixtures import helper\nfrom . import names\n"),
        ):
            module = tmp_path / relative
            module.parent.mkdir(parents=True, exist_ok=True)
            module.write_text(source, encoding="utf-8")
        policy: dict[str, object] = {
            "owner": "quality",
            "risk_model": "a tool cycle or test import erases repository ownership",
            "measurement": "parse repository tool imports",
            "false_positive_cost": "a legitimate tool dependency keeps one direction",
            "remediation": "move the shared behavior to its owning tool domain",
            "review_condition": "reassess when the repository topology changes",
            "source_roots": ["src/openai_responses_proxy", "tools"],
            "test_roots": ["tests"],
            "package_root": "src/openai_responses_proxy",
            "package_root_modules": [],
            "root_configuration_modules": [],
            "package_initializers": "ordinary-modules",
            "control_nesting": {"max_depth": 5},
            "product_concepts": {"relay": "src/openai_responses_proxy/relay/owner.py"},
            "external_responsibilities": {"credentials": "client-control-plane"},
            "allowed_package_edges": {"relay": []},
        }

        assert architecture_gaps(tmp_path, policy) == [
            "architecture_cycle:tools/forge,tools/release",
            "architecture_test_import:tests:tools/quality/audit.py",
        ]

    def test_repository_policy_declares_positive_owners_and_one_control_depth_boundary(
        self,
    ) -> None:
        policy = tomllib.loads(
            (ROOT / ".config/checks/architecture/policy.toml").read_text(encoding="utf-8")
        )
        assert policy["source_roots"] == ["src/openai_responses_proxy", "tools"]
        assert policy["test_roots"] == ["tests"]
        assert policy["package_root"] == "src/openai_responses_proxy"
        assert policy["product_concepts"] == {
            "cli-presentation": "src/openai_responses_proxy/cli/presentation.py",
            "lifecycle-transactions": "src/openai_responses_proxy/lifecycle/transaction/execution.py",
            "native-supervision": (
                "src/openai_responses_proxy/lifecycle/supervision/native_service.py"
            ),
            "payload-generations": "src/openai_responses_proxy/lifecycle/generation.py",
            "portable-responses": "src/openai_responses_proxy/protocol/replay/projection.py",
            "provider-adaptation": "src/openai_responses_proxy/providers/registry.py",
            "relay-transport": "src/openai_responses_proxy/relay/exchange.py",
            "request-admission": "src/openai_responses_proxy/relay/admission.py",
            "runtime-configuration": "src/openai_responses_proxy/runtime/config.py",
        }
        assert policy["external_responsibilities"] == {
            "client-configuration": "client-control-plane",
            "conversation-history": "client",
            "credentials": "client-control-plane",
            "model-selection": "client",
            "provider-selection": "client-control-plane",
        }
        assert set(policy["allowed_package_edges"]) == {
            "cli",
            "lifecycle",
            "protocol",
            "providers",
            "relay",
            "runtime",
            "serving",
        }
        assert set(policy) == {
            "owner",
            "risk_model",
            "measurement",
            "false_positive_cost",
            "remediation",
            "review_condition",
            "source_roots",
            "test_roots",
            "package_root",
            "package_root_modules",
            "root_configuration_modules",
            "package_initializers",
            "product_concepts",
            "external_responsibilities",
            "allowed_package_edges",
            "control_nesting",
        }
        assert all(
            isinstance(policy[field], str) and policy[field].strip()
            for field in (
                "owner",
                "risk_model",
                "measurement",
                "false_positive_cost",
                "remediation",
                "review_condition",
            )
        )

    def test_package_initializer_contract_is_explicit_and_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "openai_responses_proxy"
            (package / "serving").mkdir(parents=True)
            (package / "serving" / "__init__.py").write_text(
                '"""Serving package."""\nVALUE = 1\n', encoding="utf-8"
            )
            policy = {
                "owner": "quality",
                "risk_model": "package initializers can hide runtime coupling",
                "measurement": "parse declared package initializers",
                "false_positive_cost": "a deliberate initializer requires policy review",
                "remediation": "move runtime behavior to a semantic module",
                "review_condition": "reassess when initializer behavior is required",
                "source_roots": ["src/openai_responses_proxy"],
                "test_roots": ["tests"],
                "package_root": "src/openai_responses_proxy",
                "package_root_modules": [],
                "root_configuration_modules": [],
                "package_initializers": "declarations-only",
                "control_nesting": {"max_depth": 5},
                "product_concepts": {
                    "native-supervision": "src/openai_responses_proxy/serving/native.py"
                },
                "external_responsibilities": {"credentials": "client-control-plane"},
                "allowed_package_edges": {"serving": []},
            }
            gaps = architecture_gaps(root, policy)
            policy["package_initializers"] = "ordinary-modules"
            relaxed = architecture_gaps(root, policy)
        expected = "architecture_init_behavior:src/openai_responses_proxy/serving/__init__.py"
        assert expected in gaps
        assert expected not in relaxed

    def test_product_concepts_require_distinct_existing_product_owners(self) -> None:
        policy = tomllib.loads(
            (ROOT / ".config/checks/architecture/policy.toml").read_text(encoding="utf-8")
        )
        duplicate = dict(policy)
        duplicate["product_concepts"] = dict(policy["product_concepts"])
        duplicate["product_concepts"]["relay-transport"] = duplicate["product_concepts"][
            "request-admission"
        ]
        missing = dict(policy)
        missing["product_concepts"] = dict(policy["product_concepts"])
        missing["product_concepts"]["relay-transport"] = (
            "src/openai_responses_proxy/relay/missing.py"
        )

        assert (
            "architecture_concept_owner_reused:src/openai_responses_proxy/relay/admission.py"
            in (architecture_gaps(ROOT, duplicate))
        )
        assert "architecture_concept_owner_missing:relay-transport" in architecture_gaps(
            ROOT, missing
        )

    @pytest.mark.parametrize(
        ("override", "expected"),
        [
            ({"owner": " "}, ["architecture_policy_value:owner"]),
            (
                {"package_initializers": "implicit"},
                ["architecture_policy_value:package_initializers"],
            ),
            ({"package_root": "src/absent"}, ["architecture_package_missing:src/absent"]),
            ({"product_concepts": {}}, ["architecture_product_concepts_empty"]),
            (
                {"product_concepts": {"relay": "owner.txt"}},
                ["architecture_concept_owner_outside_product:relay"],
            ),
            (
                {"external_responsibilities": {}},
                ["architecture_external_responsibilities_empty"],
            ),
        ],
    )
    def test_architecture_gate_rejects_an_incomplete_policy_or_product(
        self, tmp_path: Path, override: dict[str, object], expected: list[str]
    ) -> None:
        owner = tmp_path / "src/openai_responses_proxy/relay/owner.py"
        owner.parent.mkdir(parents=True)
        owner.write_text("VALUE = 1\n", encoding="utf-8")
        (tmp_path / "owner.txt").write_text("outside the product package\n", encoding="utf-8")
        policy: dict[str, object] = {
            "owner": "quality",
            "risk_model": "an unowned or absent product escapes topology review",
            "measurement": "evaluate the declared policy against the checkout",
            "false_positive_cost": "a deliberate omission requires architecture review",
            "remediation": "complete the declaration or restore the product owner",
            "review_condition": "reassess when the product topology changes",
            "source_roots": ["src/openai_responses_proxy"],
            "test_roots": ["tests"],
            "package_root": "src/openai_responses_proxy",
            "package_root_modules": [],
            "root_configuration_modules": [],
            "package_initializers": "ordinary-modules",
            "control_nesting": {"max_depth": 5},
            "product_concepts": {"relay": "src/openai_responses_proxy/relay/owner.py"},
            "external_responsibilities": {"credentials": "client-control-plane"},
            "allowed_package_edges": {"relay": []},
        }

        assert architecture_gaps(tmp_path, policy) == []
        assert architecture_gaps(tmp_path, {**policy, **override}) == expected


def test_semantic_packages_are_declarative_and_facade_free():
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


def test_runtime_context_has_one_semantic_owner():
    assert (ROOT / "src/openai_responses_proxy/lifecycle/context.py").is_file()


def test_provider_specific_wire_policies_have_a_semantic_owner():
    assert (ROOT / "src/openai_responses_proxy/providers/policies/dmxapi.py").is_file()
