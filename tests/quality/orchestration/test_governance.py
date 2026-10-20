"""Governance graph, formatter and failed-command orchestration."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT
from tools.quality import governance


@pytest.mark.parametrize("failure", [OSError("missing"), subprocess.CompletedProcess([], 1)])
def test_governance_failure_stops_before_subsequent_commands(failure, mocker, capsys):
    mocker.patch.object(governance, "_commands", return_value=(("first",), ("second",)))
    run = mocker.patch.object(governance.subprocess, "run")
    if isinstance(failure, OSError):
        run.side_effect = failure
    else:
        run.return_value = failure
    with pytest.raises(SystemExit) as stopped:
        governance.main(())
    assert stopped.value.code == 1
    assert run.call_count == 1
    assert "first" in capsys.readouterr().err


@pytest.mark.parametrize(
    "policy",
    ['known_first_party = "openai_responses_proxy"\n', "package_module_name_map = []\n"],
)
def test_malformed_dependency_policy_stops_governance(
    policy: str, tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(policy, encoding="utf-8")
    mocker.patch.object(governance, "DEPENDENCY_POLICY", path)

    with pytest.raises(governance.GovernanceError, match="dependency policy is malformed"):
        governance._dependency_options()


def test_governance_fix_writes_the_inputs_that_audit_checks(mocker: MockerFixture) -> None:
    mocker.patch.object(
        governance, "_tracked_current", side_effect=[("README.md",), ("pyproject.toml",)]
    )
    inputs = (".config/ci/graph/proof.cue", ".config/ci/peers/gitlab/pipeline.cue")
    mocker.patch.object(governance, "model_inputs", return_value=inputs)
    run = mocker.patch.object(
        governance.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    )

    governance.main(("--fix",))

    assert [tuple(call.args[0]) for call in run.call_args_list] == [
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "prettier",
            "--write",
            "--config",
            ".config/checks/prettier/prettier.toml",
            "--no-editorconfig",
            "README.md",
        ),
        ("taplo", "format", "--config", ".config/checks/taplo/taplo.toml", "pyproject.toml"),
        ("cue", "fmt", "--files", *inputs),
    ]


def test_dependency_and_dead_code_policy_has_one_precise_owner() -> None:
    dependency = tomllib.loads(
        (ROOT / ".config/checks/deptry/policy.toml").read_text(encoding="utf-8")
    )
    assert dependency == {
        "known_first_party": ["openai_responses_proxy"],
        "package_module_name_map": {"pyinstaller": ["PyInstaller"]},
    }
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "deptry" not in pyproject["tool"]

    dead_code = tomllib.loads(
        (ROOT / ".config/checks/vulture/vulture.toml").read_text(encoding="utf-8")
    )["tool"]["vulture"]
    assert dead_code == {"min_confidence": 100, "sort_by_size": True}

    commands = governance._commands(online_links=False)
    assert sum(command[0] == "deptry" for command in commands) == 1
    assert sum(command[0] == "vulture" for command in commands) == 1
    quality_commands = tuple(
        argument
        for command in commands
        if command[0] in {"deptry", "vulture"}
        for argument in command
    )
    assert "--ignore" not in quality_commands
    assert "--exclude" not in quality_commands
    assert "--whitelist" not in quality_commands
    assert "--baseline" not in quality_commands


def test_governance_composition_owns_each_repository_check_once(mocker) -> None:
    inputs = (".config/ci/graph/proof.cue", ".config/ci/peers/gitlab/pipeline.cue")
    mocker.patch.object(governance, "model_inputs", return_value=inputs)
    completed = mocker.Mock(returncode=0)
    tracked = mocker.Mock(
        returncode=0,
        stdout=b"README.md\0.gitlab-ci.yml\0.config/mise/config.toml\0openspec/changes/archive/old.md\0",
    )
    run = mocker.patch.object(
        governance.subprocess,
        "run",
        side_effect=[tracked, tracked, *([completed] * 15)],
    )

    governance.audit(online_links=False)

    commands = [tuple(call.args[0]) for call in run.call_args_list[2:]]
    assert commands == [
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "prettier",
            "--check",
            "--config",
            ".config/checks/prettier/prettier.toml",
            "--no-editorconfig",
            "README.md",
            ".gitlab-ci.yml",
        ),
        (
            "taplo",
            "format",
            "--check",
            "--config",
            ".config/checks/taplo/taplo.toml",
            ".config/mise/config.toml",
        ),
        ("cue", "fmt", "--check", "--files", *inputs),
        ("cue", "vet", *inputs),
        (governance.sys.executable, "-m", "tools.ci.project"),
        (
            "npm",
            "exec",
            "--offline",
            "--",
            "openspec",
            "validate",
            "--all",
            "--strict",
            "--no-interactive",
        ),
        ("mise", "run", "--timeout", "60s", "workflow-check"),
        (
            "deptry",
            "src/openai_responses_proxy",
            "--config",
            "pyproject.toml",
            "--known-first-party",
            "openai_responses_proxy",
            "--package-module-name-map",
            "pyinstaller=PyInstaller",
            "--no-ansi",
        ),
        (
            "vulture",
            "src/openai_responses_proxy",
            "tools",
            "--config",
            ".config/checks/vulture/vulture.toml",
        ),
        (
            "gitleaks",
            "git",
            "--config",
            ".config/checks/gitleaks/gitleaks.toml",
            "--platform",
            "gitlab",
            "--redact",
            "--no-banner",
            ".",
        ),
        (
            governance.sys.executable,
            "-m",
            "pytest",
            "-q",
            "--rootdir",
            ".",
            "-c",
            ".config/checks/pytest/pytest.toml",
            "tests/quality/secrets",
        ),
        (
            "lychee",
            "--config",
            ".config/checks/lychee/lychee.toml",
            "--offline",
            "README.md",
        ),
        (governance.sys.executable, "-m", "tools.release.metadata"),
        (governance.sys.executable, "-m", "tools.quality.hard_coding"),
        (governance.sys.executable, "-m", "tools.quality.repository"),
    ]


def test_structured_text_formatters_have_disjoint_native_ownership() -> None:
    policy = tomllib.loads(
        (ROOT / ".config/checks/responsibilities/policy.toml").read_text(encoding="utf-8")
    )
    concerns = {concern["id"]: concern for concern in policy["concerns"]}

    assert concerns["markdown-yaml-format"] == {
        "id": "markdown-yaml-format",
        "owner": "prettier",
        "scope": "prettier-formatted",
        "session": "governance",
        "configuration": [".config/checks/prettier/prettier.toml"],
        "risk_model": concerns["markdown-yaml-format"]["risk_model"],
        "measurement": concerns["markdown-yaml-format"]["measurement"],
        "false_positive_cost": concerns["markdown-yaml-format"]["false_positive_cost"],
        "remediation": concerns["markdown-yaml-format"]["remediation"],
        "review_condition": concerns["markdown-yaml-format"]["review_condition"],
    }
    assert concerns["toml-format"]["owner"] == "taplo"
    assert concerns["toml-format"]["scope"] == "toml-formatted"
    assert concerns["toml-format"]["configuration"] == [".config/checks/taplo/taplo.toml"]
    assert concerns["cue-format"]["owner"] == "cue"
    assert concerns["cue-format"]["scope"] == "cue-formatted"
    assert concerns["cue-format"]["configuration"] == [
        ".config/ci/graph/proof.cue",
        "tools/ci/project.py",
    ]

    formatter_inputs = (
        set(governance._tracked_current((".md", ".yaml", ".yml"))),
        set(governance._tracked_current((".toml",))),
        set(governance.model_inputs()),
    )
    for index, inputs in enumerate(formatter_inputs):
        assert inputs
        assert all(inputs.isdisjoint(other) for other in formatter_inputs[index + 1 :])


@pytest.mark.repository_toolchain
@pytest.mark.parametrize("indent_size", [4, 8])
@pytest.mark.parametrize("suffix", [".md", ".yaml"])
def test_native_formatter_isolates_editorconfig_and_preserves_check_inputs(
    tmp_path: Path, indent_size: int, suffix: str
) -> None:
    editorconfig = tmp_path / ".editorconfig"
    editorconfig_source = (
        f"root = true\n\n[*]\nindent_style = space\nindent_size = {indent_size}\n"
        "end_of_line = crlf\n"
    )
    editorconfig.write_text(editorconfig_source, encoding="utf-8")
    metadata = "relations:\n    part_of: ../README.md\n"
    body = "# Native formatting\n\nA wrapped paragraph\nkeeps its lines.\n\n- One\n- Two\n"
    original = f"---\n{metadata}---\n\n{body}" if suffix == ".md" else metadata
    canonical_metadata = "relations:\n  part_of: ../README.md\n"
    canonical = (
        f"---\n{canonical_metadata}---\n\n{body}" if suffix == ".md" else canonical_metadata
    ).encode()
    document = tmp_path / f"native-format{suffix}"
    document.write_text(original, encoding="utf-8")

    for write, expected_status in [(False, 1), (True, 0), (True, 0), (False, 0)]:
        before = document.read_bytes()
        command = governance._formatter_commands((str(document),), (), write=write)[0]
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == expected_status, result.stdout + result.stderr
        assert document.read_bytes() == (canonical if write else before)
    assert document.read_bytes() == canonical
    assert editorconfig.read_text(encoding="utf-8") == editorconfig_source
