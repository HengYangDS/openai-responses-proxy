"""Enforce independent source-root coverage and semantic-package observation."""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from pathlib import PurePosixPath
from typing import Annotated

from coverage import Coverage
from cyclopts import App
from cyclopts import Parameter

ROOT = Path(__file__).resolve().parents[2]
_POLICY_KEYS = {
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


def _object_mapping(value: object, *, label: str) -> dict[str, object]:
    """Narrow one decoded mapping to string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _ratio_gaps(
    totals: Mapping[str, object],
    floor: float,
    *,
    total_key: str,
    covered_key: str,
    label: str,
    missing_gap: str,
) -> list[str]:
    """Validate one exact measured ratio and report its stable coverage gap."""
    total = totals.get(total_key)
    covered = totals.get(covered_key)
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or isinstance(covered, bool)
        or not isinstance(covered, int)
        or not 0 <= covered <= total
    ):
        return [missing_gap]
    threshold = Decimal(str(floor))
    actual = Decimal(covered * 100) / Decimal(total)
    if actual <= threshold:
        return [f"{label}_coverage_not_above_floor:{actual:.2f}<={threshold:.2f}"]
    return []


def branch_gaps(totals: Mapping[str, object], floor: float) -> list[str]:
    """Return a gap when exact branch coverage is absent or not above ``floor``."""
    return _ratio_gaps(
        totals,
        floor,
        total_key="num_branches",
        covered_key="covered_branches",
        label="branch",
        missing_gap="branch_coverage_requires_measured_branches",
    )


def statement_gaps(totals: Mapping[str, object], floor: float) -> list[str]:
    """Return a gap when exact statement coverage is absent or not above ``floor``."""
    return _ratio_gaps(
        totals,
        floor,
        total_key="num_statements",
        covered_key="covered_lines",
        label="statement",
        missing_gap="statement_coverage_requires_measured_statements",
    )


def measured_report(coverage: Coverage) -> dict[str, object]:
    """Return the exact configured coverage report."""
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "coverage.json"
        coverage.json_report(outfile=str(report))
        return _object_mapping(
            json.loads(report.read_text(encoding="utf-8")),
            label="coverage JSON report",
        )


def _semantic_package(path: str, package_marker: str) -> str | None:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if parts[-1] == f"{package_marker}.py":
        return "root"
    try:
        index = parts.index(package_marker)
    except ValueError:
        return None
    relative = parts[index + 1 :]
    return relative[0] if len(relative) > 1 else "root"


def package_totals(files: Mapping[str, object], package_marker: str) -> dict[str, dict[str, int]]:
    """Aggregate exact coverage counts by the declared semantic package root."""
    totals: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {
            "num_statements": 0,
            "covered_lines": 0,
            "num_branches": 0,
            "covered_branches": 0,
        }
    )
    for path, detail in files.items():
        package = _semantic_package(path, package_marker)
        summary = detail.get("summary") if isinstance(detail, dict) else None
        if package is None or not isinstance(summary, dict):
            continue
        for key in totals[package]:
            value = summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[package][key] += value

    return {package: totals[package] for package in sorted(totals)}


def package_gaps(totals: Mapping[str, Mapping[str, int]]) -> list[str]:
    """Require non-zero execution evidence for every semantic package."""
    gaps: list[str] = []
    for package in sorted(totals):
        package_totals = totals[package]
        if package_totals["num_statements"] <= 0:
            gaps.append(f"package_statement_coverage_requires_measured_statements:{package}")
        elif package_totals["covered_lines"] <= 0:
            gaps.append(f"package_statement_coverage_unobserved:{package}")
        if package_totals["num_branches"] > 0 and package_totals["covered_branches"] <= 0:
            gaps.append(f"package_branch_coverage_unobserved:{package}")
    return gaps


def source_gaps(
    files: Mapping[str, object], sources: tuple[str, ...], floor: float
) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Admit every native-configured source independently of unrelated coverage."""
    if not sources:
        return ["coverage_sources_undeclared"], {}
    gaps = []
    report: dict[str, dict[str, object]] = {}
    for source in sources:
        packages = package_totals(files, source)
        totals = {
            key: sum(package[key] for package in packages.values())
            for key in ("num_statements", "covered_lines", "num_branches", "covered_branches")
        }
        report[source] = {**totals, "packages": packages}
        if not packages:
            gaps.append(f"coverage_source_unmeasured:{source}")
            continue
        violations = [
            *statement_gaps(totals, floor),
            *(branch_gaps(totals, floor) if totals["num_branches"] else []),
            *package_gaps(packages),
        ]
        for violation in violations:
            reason, separator, detail = violation.partition(":")
            gaps.append(f"source_{reason}:{source}{separator}{detail}")
    return gaps, report


def load_policy(path: Path) -> dict[str, object]:
    """Load the complete coverage contract without implicit defaults."""
    policy = _object_mapping(
        tomllib.loads(path.read_text(encoding="utf-8")),
        label="coverage policy",
    )
    if set(policy) != _POLICY_KEYS:
        raise ValueError("coverage policy fields do not match the canonical schema")
    floor = policy["minimum_percent"]
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 < floor < 100:
        raise ValueError("minimum_percent must be within (0, 100)")
    if policy["comparison"] != "strictly-above":
        raise ValueError("comparison must be strictly-above")
    if policy["threshold_scopes"] != ["source-root"]:
        raise ValueError("threshold_scopes must contain exactly source-root")
    if policy["package_observation"] != "required":
        raise ValueError("package_observation must be required")
    if policy["metrics"] != ["statement", "branch"]:
        raise ValueError("metrics must contain statement and branch")
    rationale = (
        "owner",
        "source",
        "risk_model",
        "measurement",
        "false_positive_cost",
        "remediation",
        "review_condition",
    )
    for key in rationale:
        value = policy[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("coverage policy rationale fields must be non-empty")
    return policy


def _command(
    *,
    policy_path: Annotated[Path, Parameter(name="--policy")],
) -> int:
    """Load current coverage data and report one machine-readable verdict."""
    policy = load_policy(policy_path)
    raw_floor = policy["minimum_percent"]
    if isinstance(raw_floor, bool) or not isinstance(raw_floor, int | float):
        raise AssertionError("validated coverage floor must be numeric")
    floor = float(raw_floor)
    coverage = Coverage(config_file=str(ROOT / ".config/quality/native/coverage.ini"))
    try:
        coverage.load()
        report = measured_report(coverage)
    finally:
        coverage.get_data().close(force=True)
    totals = _object_mapping(report.get("totals"), label="coverage totals")
    files = _object_mapping(report.get("files"), label="coverage files")
    sources = []
    for option in ("run:source_pkgs", "run:source_dirs"):
        configured = coverage.get_option(option)
        if not isinstance(configured, list) or not all(
            isinstance(item, str) for item in configured
        ):
            raise ValueError(f"{option} must declare source roots")
        sources.extend(PurePosixPath(str(item)).name for item in configured)
    gaps, source_report = source_gaps(files, tuple(sources), floor)
    print(
        json.dumps(
            {"ok": not gaps, "gaps": gaps, "sources": source_report, **totals},
            sort_keys=True,
        )
    )
    return 0 if not gaps else 1


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run the coverage policy through the repository parser stack."""
    result = App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
