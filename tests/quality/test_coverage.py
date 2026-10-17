"""Coverage admission for product, tooling and orchestration."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.quality import branch_coverage


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_percent", True),
        ("minimum_percent", float("nan")),
        ("minimum_percent", 0),
        ("minimum_percent", 100),
        ("minimum_percent", 101),
        ("comparison", "at-least"),
        ("comparison", "greater"),
        ("threshold_scopes", []),
        ("package_observation", "optional"),
        ("metrics", ["statement"]),
        ("owner", " "),
        ("unknown", "value"),
    ],
)
def test_policy_cannot_weaken_the_declared_boundary(field, value, mocker):
    path = branch_coverage.ROOT / ".config/checks/coverage/policy.toml"
    policy = tomllib.loads(path.read_text())
    policy[field] = value
    mocker.patch.object(branch_coverage.tomllib, "loads", return_value=policy)
    diagnostic = "coverage policy" if field in {"owner", "unknown"} else field
    with pytest.raises(ValueError, match=diagnostic):
        branch_coverage.load_policy(path)


@pytest.mark.parametrize("value", [None, {1: "invalid"}])
def test_report_objects_require_string_keys(value):
    with pytest.raises(ValueError, match="must be an object"):
        branch_coverage._object_mapping(value, label="measured report")


def test_empty_package_cannot_claim_observed_statements():
    assert branch_coverage.package_gaps(
        {
            "tools": {
                "num_statements": 0,
                "covered_lines": 0,
                "num_branches": 0,
                "covered_branches": 0,
            }
        }
    ) == ["package_statement_coverage_requires_measured_statements:tools"]


def test_malformed_package_counts_are_not_coverage_evidence():
    assert branch_coverage.package_totals(
        {
            "tools/quality/observed.py": {
                "summary": {
                    "num_statements": 4,
                    "covered_lines": True,
                    "num_branches": 2,
                    "covered_branches": -1,
                }
            }
        },
        "tools",
    ) == {
        "quality": {
            "num_statements": 4,
            "covered_lines": 0,
            "num_branches": 2,
            "covered_branches": 0,
        }
    }


@pytest.mark.parametrize("source", [None, [1], ["tools"]])
def test_cli_requires_native_source_inventory_and_returns_measured_success(source, mocker, capsys):
    path = branch_coverage.ROOT / ".config/checks/coverage/policy.toml"
    coverage = mocker.patch.object(branch_coverage, "Coverage").return_value
    coverage.get_option.return_value = source
    totals = {"num_statements": 1, "covered_lines": 1, "num_branches": 0, "covered_branches": 0}
    mocker.patch.object(
        branch_coverage,
        "measured_report",
        return_value={
            "totals": totals,
            "files": {"tools/check.py": {"summary": totals}},
        },
    )
    if source == ["tools"]:
        with pytest.raises(SystemExit) as stopped:
            branch_coverage.main(("--policy", str(path)))
        assert stopped.value.code == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    else:
        with pytest.raises(ValueError, match="must declare source roots"):
            branch_coverage.main(("--policy", str(path)))


class TestCoverageContracts:
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
        config = ROOT / ".config/checks/coverage/coverage.toml"
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
            branch_coverage.main(("--policy", str(ROOT / ".config/checks/coverage/policy.toml")))
        assert failure.value.code == 1
        admission = json.loads(capsys.readouterr().out)
        assert set(admission["sources"]) == {"openai_responses_proxy", "tools", "noxfile"}
        assert admission["sources"]["noxfile"]["covered_branches"] == 2
        assert len(admission["gaps"]) == 4
        gc.collect()

    def test_coverage_floor_has_one_owner_and_semantic_risk_scopes(self) -> None:
        coverage = tomllib.loads(
            (ROOT / ".config/checks/coverage/coverage.toml").read_text(encoding="utf-8")
        )
        policy = tomllib.loads(
            (ROOT / ".config/checks/coverage/policy.toml").read_text(encoding="utf-8")
        )
        assert coverage["run"]["branch"] is True
        assert coverage["run"]["source_pkgs"] == ["openai_responses_proxy", "noxfile"]
        assert "omit" not in coverage["run"]
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
