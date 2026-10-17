"""Repository quality-policy ownership and source inventory contracts."""

from __future__ import annotations

import re
import tomllib

import pytest
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT
from tests.quality.fixtures import git as _git
from tools.quality.repository import __main__ as repository_audit


def test_pytest_owns_temporary_directory_retirement(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("tmp_path_retention_policy") == "none"
    assert int(pytestconfig.getini("tmp_path_retention_count")) == 0


def test_current_repository_policy_is_internally_consistent() -> None:
    report = repository_audit.audit()
    assert report["policy_errors"] == []
    inventory_gaps = [gap for gap in report["gaps"] if gap.startswith("quality_inventory_")]
    untracked = _git(
        ROOT,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        "--",
        "*.py",
        "openai_responses_proxy",
        "watchdog",
        "tools",
        "tests",
    ).stdout
    expected_untracked = sorted(
        path.decode() for path in untracked.split(b"\0") if path.endswith(b".py")
    )
    expected_gaps = []
    missing = sorted(
        path.decode()
        for path in _git(
            ROOT,
            "ls-files",
            "-z",
            "--deleted",
            "--",
            "*.py",
            "openai_responses_proxy",
            "watchdog",
            "tools",
            "tests",
        ).stdout.split(b"\0")
        if path.endswith(b".py")
    )
    if missing:
        expected_gaps.append(f"quality_inventory_missing:{','.join(missing)}")
    if expected_untracked:
        expected_gaps.append(f"quality_inventory_untracked:{','.join(expected_untracked)}")
    assert inventory_gaps == expected_gaps
    assert len(report["files"]) > 20
    inventoried = {entry["path"] for entry in report["files"]}
    for path in (
        "src/openai_responses_proxy/lifecycle/control.py",
        "src/openai_responses_proxy/lifecycle/supervision/watchdog.py",
        "tools/release/metadata.py",
        "tests/governance/test_repository.py",
    ):
        assert path in inventoried


def test_quality_policy_has_one_explicit_owner_per_concern() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tool = pyproject.get("tool", {})

    for path in (
        ".config/checks/ruff/ruff.toml",
        ".config/checks/pytest/pytest.toml",
        ".config/checks/ty/ty.toml",
        ".config/checks/coverage/coverage.toml",
        ".config/checks/coverage/policy.toml",
        ".config/checks/architecture/policy.toml",
        ".config/checks/text-layout/policy.toml",
        ".config/checks/lychee/lychee.toml",
        ".config/checks/gitleaks/gitleaks.toml",
        ".config/checks/deptry/policy.toml",
        ".editorconfig",
    ):
        assert (ROOT / path).is_file(), path

    for duplicate in ("ruff", "pytest", "ty", "coverage", "deptry"):
        assert duplicate not in tool
    repository = tool.get("openai-responses-proxy", {})
    assert "quality" not in repository
    assert not (ROOT / "pytest.ini").exists()
    assert not (ROOT / ".gitleaks.toml").exists()

    governance = " ".join(
        (ROOT / "docs/governance/release-and-change-policy.md").read_text(encoding="utf-8").split()
    )
    assert (
        "`.config/checks/pytest/pytest.toml` therefore owns test discovery and warning policy"
        in governance
    )
    assert "`.config/quality/policy/` owns quality policy" not in governance

    ruff = tomllib.loads((ROOT / ".config/checks/ruff/ruff.toml").read_text(encoding="utf-8"))
    selected = ruff["lint"]["select"]
    assert len(selected) == len(set(selected))
    assert {"F", "I", "N", "PT", "UP", "B", "RET", "PERF", "RUF"} <= set(selected)
    assert "ANN" not in selected
    assert any(rule == "S" or rule.startswith("S") for rule in selected)
    assert "D" in selected
    assert "ignore" not in ruff["lint"]
    assert ruff["lint"]["pydocstyle"] == {"convention": "google"}
    assert ruff["lint"]["per-file-ignores"] == {"tests/**": ["D1"]}

    rationale = {
        "risk_model",
        "measurement",
        "false_positive_cost",
        "remediation",
        "review_condition",
    }
    for relative in (
        ".config/checks/architecture/policy.toml",
        ".config/checks/coverage/policy.toml",
        ".config/checks/text-layout/policy.toml",
    ):
        policy = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))
        assert all(
            isinstance(policy.get(field), str) and policy[field].strip() for field in rationale
        ), relative


def test_editor_defaults_and_text_layout_policy_are_aligned() -> None:
    editor = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    policy = tomllib.loads(
        (ROOT / ".config/checks/text-layout/policy.toml").read_text(encoding="utf-8")
    )

    assert "charset = utf-8" in editor
    assert "end_of_line = lf" in editor
    assert "insert_final_newline = true" in editor
    assert "trim_trailing_whitespace = true" in editor
    assert policy["encoding"] == "utf-8"
    assert policy["line_ending"] == "lf"
    assert policy["insert_final_newline"] is True
    assert policy["trim_trailing_whitespace"] is True


def test_readme_install_path_matches_product_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "$OPENAI_RESPONSES_PROXY_RELEASE_ASSET" not in readme
    assert "$OPENAI_RESPONSES_PROXY_RELEASE_TRUST_ANCHOR" not in readme
    assert "openai-responses-proxy-<version>-macos-arm64.tar.gz" in readme
    assert re.search(r"openai-responses-proxy-\d+\.\d+\.\d+-", readme) is None
    assert "Replace `<version>` with the release version you downloaded." in readme
    assert "openai-responses-proxy-macos-arm64.manifest.json" in readme
    assert "SHA256SUMS.sig" in readme
    assert "SSH" in readme
    assert "`allowed_signers` file" in readme
    assert "--port 8801" in readme
    assert "OPENAI_RESPONSES_PROXY_PROXY_PORT" not in readme


def test_python_command_surfaces_use_one_modern_parser() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    assert any(requirement.startswith("cyclopts==") for requirement in dependencies)
    offenders = []
    for root in (ROOT / "src", ROOT / "tools"):
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "import argparse" in source or "from argparse" in source:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_runtime_dependencies_are_exactly_pinned() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]

    assert all(
        name and separator == "==" and version
        for name, separator, version in (
            requirement.partition("==") for requirement in dependencies
        )
    )


def test_repository_cli_is_quiet_on_success_and_diagnostic_on_failure(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(repository_audit, "audit", return_value={"ok": True, "gaps": []})
    output = mocker.patch("builtins.print")
    repository_audit.main()
    output.assert_not_called()

    mocker.patch.object(
        repository_audit, "audit", return_value={"ok": False, "gaps": ["invalid_contract"]}
    )
    output.reset_mock()
    with pytest.raises(SystemExit):
        repository_audit.main()
    output.assert_called_once()
