"""Coverage admission validates policy and measured source boundaries."""

import json
import tomllib

import pytest

from tools.quality import branch_coverage


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_percent", True),
        ("minimum_percent", float("nan")),
        ("minimum_percent", 0),
        ("minimum_percent", 101),
        ("comparison", "greater"),
        ("threshold_scopes", []),
        ("package_observation", "optional"),
        ("metrics", ["statement"]),
        ("owner", " "),
        ("unknown", "value"),
    ],
)
def test_policy_cannot_weaken_the_declared_boundary(field, value, mocker):
    path = branch_coverage.ROOT / ".config/quality/policy/coverage.toml"
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
    path = branch_coverage.ROOT / ".config/quality/policy/coverage.toml"
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
