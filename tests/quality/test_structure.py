"""Structural and coverage contracts for repository quality."""

from __future__ import annotations

import ast
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import tomllib
from functools import partial
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tests.quality.fixtures import audit_source
from tests.quality.fixtures import git
from tests.quality.fixtures import quality_inventory
from tests.quality.fixtures import repository
from tools.quality import branch_coverage
from tools.quality import governance
from tools.quality import responsibilities
from tools.quality import text_layout
from tools.quality.repository import __main__ as repository_audit
from tools.quality.repository.names import semantic_name_gaps
from tools.quality.repository.topology import architecture_gaps


class TestStructuralQualityContracts:
    def test_native_coverage_measures_product_tools_and_orchestration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package = tmp_path / "openai_responses_proxy"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        observed = package / "observed.py"
        behavior = "def choose(value):\n    if value:\n        return 1\n    return 0\n"
        observed.write_text(behavior, encoding="utf-8")
        (package / "unexecuted.py").write_text(behavior, encoding="utf-8")
        tooling = tmp_path / "tools/quality"
        tooling.mkdir(parents=True)
        (tooling / "observed.py").write_text(behavior, encoding="utf-8")
        (tooling / "unexecuted.py").write_text(behavior, encoding="utf-8")
        (tmp_path / "noxfile.py").write_text(behavior, encoding="utf-8")
        driver = tmp_path / "probe.py"
        driver.write_text(
            "from openai_responses_proxy import observed as product\n"
            "from tools.quality import observed as tooling\nimport noxfile\n"
            "for owner in (product, tooling, noxfile):\n"
            "    owner.choose(True)\n    owner.choose(False)\n",
            encoding="utf-8",
        )
        config = ROOT / ".config/quality/native/coverage.ini"
        data_file = tmp_path / ".coverage"
        environment = {**os.environ, "COVERAGE_FILE": str(data_file)}
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", "--rcfile", str(config), str(driver)],
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "coverage", "json", "--rcfile", str(config), "-o", "-"],
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        files = {Path(name): detail for name, detail in json.loads(result.stdout)["files"].items()}
        assert set(files) == {
            Path(name)
            for name in (
                "openai_responses_proxy/__init__.py",
                "openai_responses_proxy/observed.py",
                "openai_responses_proxy/unexecuted.py",
                "tools/quality/observed.py",
                "tools/quality/unexecuted.py",
                "noxfile.py",
            )
        }
        for name in ("openai_responses_proxy/unexecuted.py", "tools/quality/unexecuted.py"):
            assert files[Path(name)]["summary"]["num_statements"] == 4
            assert files[Path(name)]["summary"]["covered_lines"] == 0
        monkeypatch.setenv("COVERAGE_FILE", str(data_file))
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as failure:
            branch_coverage.main(("--policy", str(ROOT / ".config/quality/policy/coverage.toml")))
        assert failure.value.code == 1
        admission = json.loads(capsys.readouterr().out)
        assert set(admission["sources"]) == {"openai_responses_proxy", "tools", "noxfile"}
        assert admission["sources"]["noxfile"]["covered_branches"] == 2
        assert len(admission["gaps"]) == 4
        gc.collect()

    def test_python_data_models_do_not_escape_through_any(self) -> None:
        offenders = []
        for root in (ROOT / "src", ROOT / "tools", ROOT / "tests"):
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                if any(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "typing"
                    and any(alias.name == "Any" for alias in node.names)
                    for node in ast.walk(tree)
                ):
                    offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == []

    def test_product_and_tests_never_mutate_import_resolution_or_suppress_analysis(
        self,
    ) -> None:
        offenders = []
        for root in (ROOT / "tests",):
            for path in root.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                if any(
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "sys"
                    and node.attr == "path"
                    for node in ast.walk(tree)
                ):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:sys.path")
                comments = (
                    token.string
                    for token in tokenize.generate_tokens(io.StringIO(source).readline)
                    if token.type == tokenize.COMMENT
                )
                if any("noqa" in comment or "type: ignore" in comment for comment in comments):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:suppression")
        assert offenders == []

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
            "product_concepts": {"relay": "src/openai_responses_proxy/relay/owner.py"},
            "external_responsibilities": {"credentials": "client-control-plane"},
            "allowed_package_edges": {"relay": []},
        }

        assert architecture_gaps(tmp_path, policy) == [
            "architecture_cycle:tools/forge,tools/release",
            "architecture_test_import:tests:tools/quality/audit.py",
        ]

    @pytest.mark.parametrize(
        "source",
        ["value = call(first, second)\n", "value = call(\n first,\n second,\n)\n"],
    )
    def test_logical_statement_metric_is_invariant_to_formatter_wrapping(self, source: str) -> None:
        gaps, inventory = audit_source(source)
        assert gaps == []
        assert inventory[0]["logical_statements"] == 1

    def test_structural_inventory_exposes_eloc_function_size_and_nesting(self) -> None:
        gaps, inventory = audit_source(
            "def owner(value):\n    if value:\n        return value\n    return None\n"
        )
        assert gaps == []
        assert inventory[0]["effective_lines"] == 4
        assert inventory[0]["max_function_lines"] == 4
        assert inventory[0]["max_nesting_depth"] == 1

    @pytest.mark.parametrize(
        ("source", "effective_lines", "function_lines"),
        [
            ("value = 1  # inline comment\n# comment-only line\n", 1, 0),
            ('"""Module documentation."""; value = "retained"\n', 1, 0),
            (
                'def owner():\n    """Summary.\n\n    Detail.\n    """\n\n'
                "    value = 1  # inline comment\n    # comment-only line\n    return value\n",
                3,
                3,
            ),
            ('value = """first\n\nlast"""\n', 2, 0),
        ],
        ids=("inline-comment", "docstring-with-code", "function-docstring", "multiline-data"),
    )
    def test_file_and_function_eloc_share_executable_line_semantics(
        self, source: str, effective_lines: int, function_lines: int
    ) -> None:
        _, inventory = audit_source(source)
        assert inventory[0]["effective_lines"] == effective_lines
        assert inventory[0]["max_function_lines"] == function_lines

    def test_structural_metrics_are_observations_not_merge_thresholds(self) -> None:
        source = "\n".join(
            [
                "def owner(value):",
                *[f"    value += {index}" for index in range(120)],
                "    return value",
            ]
        )
        gaps, inventory = audit_source(source)
        assert gaps == []
        assert inventory[0]["logical_statements"] == 122
        assert inventory[0]["effective_lines"] == 122

    def test_coverage_floor_has_one_owner_and_semantic_risk_scopes(self) -> None:
        coverage = (ROOT / ".config/quality/native/coverage.ini").read_text(encoding="utf-8")
        policy = tomllib.loads(
            (ROOT / ".config/quality/policy/coverage.toml").read_text(encoding="utf-8")
        )
        assert "branch = True" in coverage
        assert "source_pkgs = openai_responses_proxy" in coverage
        assert "omit" not in coverage
        assert set(policy) == {
            "minimum_percent",
            "comparison",
            "threshold_scopes",
            "package_observation",
            "metrics",
            "owner",
            "source",
            "risk_model",
            "measurement",
            "false_positive_cost",
            "remediation",
            "review_condition",
        }
        assert policy["minimum_percent"] == 95.0
        assert policy["comparison"] == "strictly-above"
        assert policy["threshold_scopes"] == ["source-root"]
        assert policy["package_observation"] == "required"

    def test_coverage_admission_keeps_each_configured_source_root_independent(self) -> None:
        files: dict[str, object] = {
            "openai_responses_proxy/relay/exchange.py": {
                "summary": {
                    "num_statements": 1000,
                    "covered_lines": 1000,
                    "num_branches": 1000,
                    "covered_branches": 1000,
                }
            },
            "tools/release/publish.py": {
                "summary": {
                    "num_statements": 10,
                    "covered_lines": 1,
                    "num_branches": 10,
                    "covered_branches": 1,
                }
            },
            "noxfile.py": {
                "summary": {
                    "num_statements": 10,
                    "covered_lines": 10,
                    "num_branches": 10,
                    "covered_branches": 10,
                }
            },
        }

        gaps, sources = branch_coverage.source_gaps(
            files, ("openai_responses_proxy", "noxfile", "tools"), 95.0
        )

        assert gaps == [
            "source_statement_coverage_not_above_floor:tools:10.00<=95.00",
            "source_branch_coverage_not_above_floor:tools:10.00<=95.00",
        ]
        assert sources["noxfile"]["num_statements"] == 10
        assert sources["tools"]["packages"] == {
            "release": {
                "num_statements": 10,
                "covered_lines": 1,
                "num_branches": 10,
                "covered_branches": 1,
            }
        }

    def test_coverage_admission_rejects_a_source_root_exactly_at_the_floor(self) -> None:
        files: dict[str, object] = {
            "tools/release/publish.py": {
                "summary": {
                    "num_statements": 3330,
                    "covered_lines": 3259,
                    "num_branches": 1140,
                    "covered_branches": 1083,
                }
            }
        }

        gaps, _ = branch_coverage.source_gaps(files, ("tools",), 95.0)

        assert gaps == ["source_branch_coverage_not_above_floor:tools:95.00<=95.00"]

    def test_coverage_admission_requires_every_configured_source(self) -> None:
        gaps, _ = branch_coverage.source_gaps({}, ("tools",), 95.0)
        assert gaps == ["coverage_source_unmeasured:tools"]

    def test_coverage_admission_requires_a_declared_scope(self) -> None:
        gaps, _ = branch_coverage.source_gaps({}, (), 95.0)
        assert gaps == ["coverage_sources_undeclared"]

    def test_repository_policy_declares_positive_owners_without_numeric_vetoes(
        self,
    ) -> None:
        policy = tomllib.loads(
            (ROOT / ".config/quality/policy/architecture.toml").read_text(encoding="utf-8")
        )
        assert policy["source_roots"] == ["src/openai_responses_proxy", "tools"]
        assert policy["test_roots"] == ["tests"]
        assert policy["package_root"] == "src/openai_responses_proxy"
        assert policy["product_concepts"] == {
            "cli-presentation": "src/openai_responses_proxy/cli/presentation.py",
            "lifecycle-transactions": "src/openai_responses_proxy/lifecycle/transaction.py",
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

    def test_git_subprocess_isolation_has_one_semantic_owner(self) -> None:
        owner = ROOT / "tools/git_environment.py"
        isolated_config_consumers = (
            ROOT / "tools/forge/audit.py",
            ROOT / "tests/forge/test_audit.py",
            ROOT / "tests/quality/fixtures.py",
        )
        immutable_proof_consumers = (ROOT / "tools/release/publication/git.py",)

        assert owner.is_file()
        assert owner.read_text(encoding="utf-8").count("GIT_CONFIG_NOSYSTEM") == 1
        for consumer in isolated_config_consumers:
            source = consumer.read_text(encoding="utf-8")
            assert "from tools.git_environment import isolated_config_environment" in source
            assert "GIT_CONFIG_NOSYSTEM" not in source
            assert "GIT_CONFIG_GLOBAL" not in source
        for consumer in immutable_proof_consumers:
            source = consumer.read_text(encoding="utf-8")
            assert "from tools.git_environment import immutable_remote_proof_environment" in source
            assert "GIT_CONFIG_NOSYSTEM" not in source
            assert "GIT_CONFIG_GLOBAL" not in source

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
            (ROOT / ".config/quality/policy/architecture.toml").read_text(encoding="utf-8")
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
            "product_concepts": {"relay": "src/openai_responses_proxy/relay/owner.py"},
            "external_responsibilities": {"credentials": "client-control-plane"},
            "allowed_package_edges": {"relay": []},
        }

        assert architecture_gaps(tmp_path, policy) == []
        assert architecture_gaps(tmp_path, {**policy, **override}) == expected

    def test_repository_has_standard_package_metadata_and_one_version_owner(
        self,
    ) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]
        assert project["name"] == "openai-responses-proxy"
        assert project["requires-python"] == ">=3.12,<3.15"
        assert project["dynamic"] == ["version"]
        assert project["scripts"] == {
            "openai-responses-proxy": "openai_responses_proxy.cli.application:main"
        }
        assert metadata["build-system"]["build-backend"] == "hatchling.build"
        assert metadata["tool"]["hatch"]["version"]["path"] == "VERSION"
        assert "setuptools" not in metadata["tool"]
        assert "requires-python" not in metadata["tool"]["openai-responses-proxy"]
        assert "version-source" not in metadata["tool"]["openai-responses-proxy"]

    @pytest.mark.parametrize(
        ("requirement", "accepted"), [(">=3.12,<3.15", True), (">=3.12", False)]
    )
    def test_repository_audit_binds_python_support_to_its_matrix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requirement: str, accepted: bool
    ) -> None:
        source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = tomllib.loads(source)["project"]["requires-python"]
        project = tmp_path / "pyproject.toml"
        project.write_text(source.replace(declared, requirement), encoding="utf-8")
        monkeypatch.setattr(repository_audit, "PROJECT", project)
        gaps = repository_audit.audit()["policy_errors"]
        assert (not any(gap.startswith("requires_python_") for gap in gaps)) is accepted

    def test_quality_inventory_uses_index_ownership_with_staged_add_and_delete(
        self,
    ) -> None:
        with repository(("src/current.py", "tests/test_current.py")) as root:
            added = root / "src" / "added.py"
            added.write_text("pass\n", encoding="utf-8")
            git(root, "add", "--", "src/added.py")
            (root / "src/current.py").unlink()
            git(root, "add", "--", "src/current.py")
            inventory = quality_inventory(root)
        assert inventory.gaps == ()
        assert [path.relative_to(root).as_posix() for path in inventory.paths] == [
            "src/added.py",
            "tests/test_current.py",
        ]

    def test_quality_inventory_requires_its_own_repository_root(self) -> None:
        with repository(("nested/src/current.py", "nested/tests/test_current.py")) as parent:
            inventory = quality_inventory(parent / "nested")
        assert inventory.paths == ()
        assert any(gap.startswith("quality_inventory_git_failed:") for gap in inventory.gaps)

    @pytest.mark.parametrize("consumer", ["responsibilities", "text", "names", "governance"])
    def test_quality_consumers_require_their_own_repository_root(
        self, consumer: str, *, monkeypatch
    ) -> None:
        with repository(("nested/README.md",)) as parent:
            root = parent / "nested"
            monkeypatch.setattr(governance, "ROOT", root)
            read = {
                "responsibilities": partial(responsibilities._tracked_paths, root),
                "text": partial(text_layout._tracked, root),
                "names": partial(semantic_name_gaps, root),
                "governance": partial(governance._tracked_current, (".md",)),
            }[consumer]
            with pytest.raises(subprocess.CalledProcessError):
                read()

    def test_quality_inventory_reads_the_linked_worktree_index(self, tmp_path: Path) -> None:
        with repository(("src/current.py", "tests/test_current.py")) as root:
            git(
                root,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "-qm",
                "fixture",
            )
            lane = tmp_path / "linked-lane"
            try:
                git(root, "worktree", "add", "--detach", str(lane))
                (lane / "src/added.py").write_text("pass\n", encoding="utf-8")
                git(lane, "add", "src/added.py")
                inventory = quality_inventory(lane)
                assert inventory.gaps == ()
                assert [path.relative_to(lane).as_posix() for path in inventory.paths] == [
                    "src/added.py",
                    "src/current.py",
                    "tests/test_current.py",
                ]
                assert "src/added.py" not in git(root, "ls-files").stdout.decode()
            finally:
                git(root, "worktree", "remove", "--force", str(lane))

    def test_quality_inventory_rejects_untracked_missing_and_out_of_scope_is_ignored(
        self,
    ) -> None:
        files = ("src/tracked.py", "tests/test_current.py", "outside/foreign.py")
        with repository(files, tracked=files[1:]) as root:
            (root / "tests/test_current.py").unlink()
            inventory = quality_inventory(root)
        assert inventory.paths == ()
        assert inventory.gaps == (
            "quality_inventory_missing:tests/test_current.py",
            "quality_inventory_untracked:src/tracked.py",
        )

    def test_quality_inventory_glob_does_not_expand_beyond_its_configured_depth(
        self,
    ) -> None:
        assert repository_audit._in_scope("owner.py", ("*.py",))
        assert not repository_audit._in_scope("nested/foreign.py", ("*.py",))
        assert repository_audit._in_scope("tools/owner.py", ("tools/*.py",))
        assert not repository_audit._in_scope("tools/nested/foreign.py", ("tools/*.py",))

    def test_quality_inventory_rejects_symlink_misnamed_test_and_empty_tests(
        self,
    ) -> None:
        with repository(("src/current.py", "tests/helper.py")) as root:
            target = root / "target.py"
            target.write_text("pass\n", encoding="utf-8")
            link = root / "src" / "link.py"
            link.symlink_to(target)
            git(root, "add", "--", "src/link.py")
            inventory = quality_inventory(root)
        assert inventory.gaps == (
            "quality_inventory_symlink:src/link.py",
            "quality_inventory_test_empty",
            "quality_inventory_test_misnamed:tests/helper.py",
        )

    def test_quality_inventory_accepts_semantic_fixture_carriers_only(self) -> None:
        files = (
            "src/current.py",
            "tests/test_current.py",
            "tests/__init__.py",
            "tests/serving/conftest.py",
            "tests/serving/fixtures.py",
            "tests/protocol/replay_fixture.py",
            "tests/nested/helper.py",
        )
        with repository(files) as root:
            inventory = quality_inventory(root)
        assert inventory.gaps == (
            "quality_inventory_test_misnamed:tests/nested/helper.py,tests/protocol/replay_fixture.py",
        )

    @pytest.mark.parametrize(
        ("totals", "floor", "expected"),
        [
            (
                {"num_branches": 20, "covered_branches": 19},
                95,
                ["branch_coverage_not_above_floor:95.00<=95.00"],
            ),
            ({"num_branches": 200, "covered_branches": 191}, 95, []),
            (
                {"num_branches": 21, "covered_branches": 20},
                96,
                ["branch_coverage_not_above_floor:95.24<=96.00"],
            ),
            (
                {"num_branches": 0, "covered_branches": 0},
                95,
                ["branch_coverage_requires_measured_branches"],
            ),
        ],
    )
    def test_branch_coverage_floor_is_enforced_independently(
        self, totals: dict[str, int], floor: int, expected: list[str]
    ) -> None:
        assert branch_coverage.branch_gaps(totals, floor) == expected

    @pytest.mark.parametrize(
        ("totals", "floor", "expected"),
        [
            (
                {"num_statements": 20, "covered_lines": 19},
                95,
                ["statement_coverage_not_above_floor:95.00<=95.00"],
            ),
            ({"num_statements": 200, "covered_lines": 191}, 95, []),
            (
                {"num_statements": 21, "covered_lines": 20},
                96,
                ["statement_coverage_not_above_floor:95.24<=96.00"],
            ),
            (
                {"num_statements": 0, "covered_lines": 0},
                95,
                ["statement_coverage_requires_measured_statements"],
            ),
        ],
    )
    def test_statement_coverage_floor_is_enforced_independently(
        self, totals: dict[str, int], floor: int, expected: list[str]
    ) -> None:
        assert branch_coverage.statement_gaps(totals, floor) == expected

    @pytest.mark.parametrize(
        ("covered", "expected"),
        [
            (1082, ["branch_coverage_not_above_floor:94.91<=95.00"]),
            (1083, ["branch_coverage_not_above_floor:95.00<=95.00"]),
            (1084, []),
        ],
    )
    def test_coverage_floor_admits_only_ratios_strictly_above_it(
        self, covered: int, expected: list[str]
    ) -> None:
        totals = {"num_branches": 1140, "covered_branches": covered}
        assert branch_coverage.branch_gaps(totals, 95) == expected

    def test_each_semantic_package_must_have_execution_evidence(self) -> None:
        files = {
            "/site-packages/openai_responses_proxy/cli/application.py": {
                "summary": {
                    "num_statements": 60,
                    "covered_lines": 58,
                    "num_branches": 20,
                    "covered_branches": 19,
                }
            },
            "/site-packages/openai_responses_proxy/relay/exchange.py": {
                "summary": {
                    "num_statements": 100,
                    "covered_lines": 99,
                    "num_branches": 100,
                    "covered_branches": 96,
                }
            },
        }
        assert (
            branch_coverage.package_gaps(
                branch_coverage.package_totals(files, "openai_responses_proxy")
            )
            == []
        )

    def test_branchless_root_package_is_governed_by_statement_coverage(self) -> None:
        files = {
            "/site-packages/openai_responses_proxy/cli/__main__.py": {
                "summary": {
                    "num_statements": 3,
                    "covered_lines": 3,
                    "num_branches": 0,
                    "covered_branches": 0,
                }
            }
        }
        assert (
            branch_coverage.package_gaps(
                branch_coverage.package_totals(files, "openai_responses_proxy")
            )
            == []
        )

    def test_files_combine_into_their_semantic_package_before_admission(self) -> None:
        files = {
            "/site-packages/openai_responses_proxy/relay/first.py": {
                "summary": {
                    "num_statements": 1,
                    "covered_lines": 0,
                    "num_branches": 0,
                    "covered_branches": 0,
                }
            },
            "/site-packages/openai_responses_proxy/relay/second.py": {
                "summary": {
                    "num_statements": 99,
                    "covered_lines": 96,
                    "num_branches": 20,
                    "covered_branches": 20,
                }
            },
        }
        assert (
            branch_coverage.package_gaps(
                branch_coverage.package_totals(files, "openai_responses_proxy")
            )
            == []
        )

    def test_semantic_package_observation_rejects_wholly_unexecuted_owner(self) -> None:
        files = {
            "/site-packages/openai_responses_proxy/relay/exchange.py": {
                "summary": {
                    "num_statements": 4,
                    "covered_lines": 0,
                    "num_branches": 2,
                    "covered_branches": 0,
                }
            }
        }
        totals = branch_coverage.package_totals(files, "openai_responses_proxy")
        assert branch_coverage.package_gaps(totals) == [
            "package_statement_coverage_unobserved:relay",
            "package_branch_coverage_unobserved:relay",
        ]
