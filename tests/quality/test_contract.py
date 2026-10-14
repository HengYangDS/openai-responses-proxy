"""Contract tests for the repository-owned Python quality policy."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tests.quality.fixtures import ROOT
from tests.quality.fixtures import git as _git
from tests.quality.fixtures import repository as _test_repository
from tools.quality import commits
from tools.quality import governance
from tools.quality.repository import __main__ as repository_audit
from tools.quality.repository.decisions import decision_record_gaps
from tools.quality.repository.names import semantic_name_gaps
from tools.quality.repository.topology import architecture_gaps


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
            "README.md",
        ),
        ("taplo", "format", "--config", ".config/checks/taplo/taplo.toml", "pyproject.toml"),
    ]


@pytest.mark.parametrize(
    "defect",
    [
        "complete",
        "metadata-navigation",
        "missing-register",
        "duplicate-sequence",
        "title",
        "status",
        "date",
        "section",
    ],
)
def test_decision_register_requires_unique_complete_records(tmp_path, defect):
    directory = tmp_path / "docs/decisions"
    directory.mkdir(parents=True)
    register = directory / "decision-register.md"
    if defect == "missing-register":
        assert decision_record_gaps(tmp_path) == ["decision_record_register_missing"]
        return
    register.write_text("[decision](dr-0001-owner.md)\n")
    text = "# DR-0001: Owner\n- Status: accepted\n- Date: 2026-01-01\n## Context\n## Decision\n## Consequences\n## Revisit Trigger\n"
    replacements = {
        "title": ("# DR-0001", "# Other"),
        "status": ("accepted", "invalid"),
        "date": ("- Date: 2026-01-01", ""),
        "section": ("## Context", ""),
    }
    if defect == "metadata-navigation":
        (directory / "README.md").write_text("# Decisions\n\n[Register](decision-register.md)\n")
        text = (
            "<!--\n---\nsubject: decision-owner\nrole: decision\n"
            "state: canonical\n---\n-->\n\n" + text
        )
    if defect in replacements:
        text = text.replace(*replacements[defect])
    (directory / "dr-0001-owner.md").write_text(text)
    if defect == "duplicate-sequence":
        (directory / "dr-0001-other.md").write_text(text)
    if defect in {"complete", "metadata-navigation"}:
        assert decision_record_gaps(tmp_path) == []
        return
    expected = {
        "duplicate-sequence": "sequence_duplicate",
        "title": "title_invalid",
        "status": "status_invalid",
        "date": "date_invalid",
        "section": "section_missing",
    }
    assert any(expected[defect] in gap for gap in decision_record_gaps(tmp_path))


def test_pytest_owns_temporary_directory_retirement(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("tmp_path_retention_policy") == "none"
    assert int(pytestconfig.getini("tmp_path_retention_count")) == 0


class TestQualityPolicyContracts:
    """Keep the repository quality scope executable rather than documentary."""

    def test_release_artifacts_have_one_construction_owner(self) -> None:
        package = importlib.util.find_spec("tools.release.artifact")
        assert package is not None
        assert package.submodule_search_locations is not None
        for concern in ("bundle", "format", "assembly", "signing", "__main__"):
            owner = importlib.util.find_spec(f"tools.release.artifact.{concern}")
            assert owner is not None

    def test_repository_checks_share_one_semantic_package(self) -> None:
        """Repository topology, naming, and decisions belong to one audit package."""
        package = importlib.util.find_spec("tools.quality.repository")
        assert package is not None
        assert package.submodule_search_locations is not None
        for concern in ("topology", "names", "decisions"):
            owner = importlib.util.find_spec(f"tools.quality.repository.{concern}")
            assert owner is not None

    def test_current_repository_policy_is_internally_consistent(self) -> None:
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

    def test_openspec_material_scope_covers_every_repository_carrier(self) -> None:
        profile = tomllib.loads((ROOT / ".ethos/profile.toml").read_text(encoding="utf-8"))

        assert profile["openspec"]["material_paths"] == ["**"]

    def test_publication_topology_has_only_declared_independent_peers(self) -> None:
        publication = tomllib.loads((ROOT / ".ethos/release.toml").read_text(encoding="utf-8"))[
            "publication"
        ]

        assert set(publication) == {
            "local_verification_command",
            "local_installation_command",
            "peers",
        }
        assert publication["peers"] == [
            {
                "id": "gitlab",
                "provider": "gitlab",
                "role": "organization_collaboration",
                "git_remote": "origin",
                "capabilities": ["repository", "ci_cd", "publication"],
                "ci_surface": ".gitlab-ci.yml",
            },
            {
                "id": "github",
                "provider": "github",
                "role": "public_distribution",
                "git_remote": "github",
                "capabilities": ["repository", "ci_cd", "publication"],
                "ci_surface": ".github/workflows/verify.yml",
            },
        ]

    def test_branch_roles_delegate_local_release_transition_to_ethos(self) -> None:
        policy = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))[
            "branch_roles"
        ]

        assert policy == {
            "release_branch": "main",
            "accepted_branch": "dev",
            "candidate_branch": "candidate/dev",
            "release_mirror": "accepted_ff",
            "work_branch_prefix": "work/",
            "proposal_branch_prefix": "proposal/",
            "canonical_sibling_worktrees": True,
        }

    def test_quality_policy_has_one_explicit_owner_per_concern(self) -> None:
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
            ".config/checks/deptry/policy.toml",
            ".editorconfig",
        ):
            assert (ROOT / path).is_file(), path

        for duplicate in ("ruff", "pytest", "ty", "coverage", "deptry"):
            assert duplicate not in tool
        repository = tool.get("openai-responses-proxy", {})
        assert "quality" not in repository
        assert not (ROOT / "pytest.ini").exists()
        pointer = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
        assert pointer == {"extend": {"path": ".config/checks/gitleaks/gitleaks.toml"}}

        governance = " ".join(
            (ROOT / "docs/governance/release-and-change-policy.md")
            .read_text(encoding="utf-8")
            .split()
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

    def test_dependency_and_dead_code_policy_has_one_precise_owner(self) -> None:
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

    def test_governance_composition_owns_each_repository_check_once(self, mocker) -> None:
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
            ("cue", "fmt", "--check", "--files", ".config/ci/pipeline.cue"),
            ("cue", "vet", ".config/ci/pipeline.cue"),
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

    def test_structured_text_formatters_have_disjoint_native_ownership(self) -> None:
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
        assert concerns["cue-format"]["configuration"] == [".config/ci/pipeline.cue"]

        scopes = {scope["id"]: scope["roles"] for scope in policy["scopes"]}
        assert set(scopes["prettier-formatted"]).isdisjoint(scopes["python"])
        assert set(scopes["toml-formatted"]).isdisjoint(scopes["python"])
        assert scopes["cue-formatted"] == ["ci-model"]

    def test_editor_defaults_and_text_layout_policy_are_aligned(self) -> None:
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

    def test_commit_policy_is_declared_for_native_hook_enforcement(self) -> None:
        workspace = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))

        assert workspace["commit_policy"]["signing_required"] is True
        assert workspace["commit_policy"]["signing_format"] == "ssh"
        assert isinstance(workspace["commit_policy"]["subject_pattern"], str)

    def test_commit_subjects_validate_an_accepted_tip(self) -> None:
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "invalid accepted subject",
            )
            tip = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            _git(root, "update-ref", "refs/heads/candidate/dev", tip)

            assert commits.commit_subject_gaps(root) == [
                "commit_subject_invalid:invalid accepted subject"
            ]

    def test_event_range_rejects_invalid_middle_commit_after_refs_advance(self, monkeypatch):
        with _test_repository(("tracked.txt",)) as root:
            revisions = []
            for subject in (
                "test(quality): baseline",
                "invalid middle subject",
                "fix(quality): valid tip",
            ):
                _git(
                    root,
                    "-c",
                    "user.name=Test Author",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    subject,
                )
                revisions.append(_git(root, "rev-parse", "HEAD").stdout.strip().decode())
            _git(root, "update-ref", "refs/heads/candidate/dev", revisions[-1])
            monkeypatch.setenv("OPENAI_RESPONSES_PROXY_COMMIT_BASE", revisions[0])
            monkeypatch.setenv("OPENAI_RESPONSES_PROXY_COMMIT_HEAD", revisions[-1])

            assert commits.commit_subject_gaps(root) == [
                "commit_subject_invalid:invalid middle subject"
            ]

    def test_zero_event_base_checks_current_commit_not_historical_subjects(self, monkeypatch):
        with _test_repository(("tracked.txt",)) as root:
            for subject in (
                "invalid historical subject",
                "fix(quality): accepted current subject",
            ):
                _git(
                    root,
                    "-c",
                    "user.name=Test Author",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    subject,
                )
            head = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            monkeypatch.setenv("OPENAI_RESPONSES_PROXY_COMMIT_BASE", "0" * len(head))
            monkeypatch.setenv("OPENAI_RESPONSES_PROXY_COMMIT_HEAD", head)

            assert commits.commit_subject_gaps(root) == []

    def test_commit_checker_derives_event_namespace_without_product_import(self, monkeypatch):
        source = (ROOT / "tools/quality/commits.py").read_text(encoding="utf-8")
        assert "from openai_responses_proxy" not in source
        monkeypatch.setattr(commits, "PROJECT", ROOT / "pyproject.toml")
        assert commits._environment_name("COMMIT_HEAD") == ("OPENAI_RESPONSES_PROXY_COMMIT_HEAD")

    @pytest.mark.parametrize(
        ("base", "head", "expected"),
        [
            (None, "tip", "commit_event_objects_invalid"),
            ("tip", None, "commit_event_objects_invalid"),
            ("--all", "tip", "commit_event_objects_invalid"),
            ("tip", "f" * 40, "commit_event_head_mismatch"),
            ("f" * 40, "tip", "commit_event_base_unavailable"),
            ("ahead", "tip", "commit_event_base_not_ancestor"),
            ("tip", "tip", "commit_subject_invalid:invalid event subject"),
            ("0" * 40, "tip", "commit_subject_invalid:invalid event subject"),
        ],
    )
    def test_event_objects_are_complete_and_checkout_bound(self, base, head, expected, monkeypatch):
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "invalid event subject",
            )
            tip = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            if base == "ahead":
                _git(
                    root,
                    "-c",
                    "user.name=Test Author",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "test(quality): future base",
                )
                base = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
                _git(root, "checkout", "--detach", tip)
            for suffix, value in (("BASE", base), ("HEAD", head)):
                name = f"OPENAI_RESPONSES_PROXY_COMMIT_{suffix}"
                if value is None:
                    monkeypatch.delenv(name, raising=False)
                else:
                    monkeypatch.setenv(name, tip if value == "tip" else value)
            assert commits.commit_subject_gaps(root) == [expected]

    def test_commit_subjects_consume_the_tracked_positive_grammar(self) -> None:
        assert commits.commit_subject_gaps(ROOT) == []

        policy = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))
        subject = commits.commit_subject_pattern(policy["commit_policy"])

        assert subject.fullmatch("refactor(quality): centralize repository policy owners")
        assert subject.fullmatch("fix(ci): provision quality projection tools")
        assert subject.fullmatch("fix(install): restore exact payload on rollback")
        assert subject.fullmatch("fix(supervision): classify zombie tombstones")
        assert subject.fullmatch("refactor(serving): rename the listener process package")
        assert not subject.fullmatch("test(service): isolate the mocked native launch contract")
        assert not subject.fullmatch("refactor: centralize repository policy owners")
        assert not subject.fullmatch("fix(arbitrary): restore exact payload on rollback")
        assert not subject.fullmatch("materialize quality-policy-ssot carrier")

    def test_commit_subject_grammar_allows_internal_semver_periods(self) -> None:
        policy = tomllib.loads((ROOT / ".ethos/workspace.toml").read_text(encoding="utf-8"))
        subject = commits.commit_subject_pattern(policy["commit_policy"])

        assert subject.fullmatch("chore(release): prepare v2.0.22")
        assert not subject.fullmatch("chore(release): prepare v2.0.22.")

    def test_commit_subjects_use_remote_main_when_candidate_is_local_only(self) -> None:
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "test(quality): establish hosted baseline",
            )
            base = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            _git(root, "update-ref", "refs/remotes/origin/main", base)
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            _git(root, "add", "tracked.txt")
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "invalid hosted subject",
            )

            assert commits.commit_subject_gaps(root) == [
                "commit_subject_invalid:invalid hosted subject"
            ]

    def test_commit_subjects_validate_head_without_an_integration_ref(self) -> None:
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "invalid root subject",
            )
            branch = _git(root, "branch", "--show-current").stdout.strip().decode()
            _git(root, "switch", "--detach", "-q")
            _git(root, "branch", "-D", branch)

            assert commits.commit_subject_gaps(root) == [
                "commit_subject_invalid:invalid root subject"
            ]

    def test_commit_subjects_bound_a_moved_integration_ref_at_its_fork_point(self) -> None:
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "invalid root subject",
            )
            (root / "tracked.txt").write_text("lane\n", encoding="utf-8")
            _git(root, "add", "tracked.txt")
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "refactor(quality): keep lane work on the stale base",
            )
            lane = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            _git(root, "checkout", "-q", "--detach", "HEAD~1")
            (root / "tracked.txt").write_text("advanced\n", encoding="utf-8")
            _git(root, "add", "tracked.txt")
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "test(quality): advance the integration base",
            )
            _git(root, "update-ref", "refs/heads/candidate/dev", "HEAD")
            _git(root, "checkout", "-q", lane)

            # The moved base is not an ancestor of HEAD, so the checker bounds
            # the lane at the fork point instead of the whole reachable history.
            assert commits.commit_subject_gaps(root) == []

    def test_commit_subjects_validate_head_at_an_integration_ref(self) -> None:
        with _test_repository(("tracked.txt",)) as root:
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "invalid root subject",
            )
            head = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            (root / "tracked.txt").write_text("candidate\n", encoding="utf-8")
            _git(root, "add", "tracked.txt")
            _git(
                root,
                "-c",
                "user.name=Test Author",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "test(quality): candidate ahead of checkout",
            )
            candidate = _git(root, "rev-parse", "HEAD").stdout.strip().decode()
            _git(root, "update-ref", "refs/heads/candidate/dev", candidate)
            _git(root, "reset", "--hard", "-q", head)

            assert commits.commit_subject_gaps(root) == [
                "commit_subject_invalid:invalid root subject"
            ]

    @pytest.mark.parametrize("policy", [{}, {"subject_pattern": " "}, {"subject_pattern": 7}])
    def test_commit_policy_without_a_subject_grammar_is_refused(self, policy) -> None:
        with pytest.raises(ValueError, match="commit_policy_subject_pattern_invalid"):
            commits.commit_subject_pattern(policy)

    def test_commit_subjects_report_an_unreadable_history(self, monkeypatch) -> None:
        for suffix in ("BASE", "HEAD"):
            monkeypatch.delenv(f"OPENAI_RESPONSES_PROXY_COMMIT_{suffix}", raising=False)
        with _test_repository(("tracked.txt",)) as root:
            gaps = commits.commit_subject_gaps(root)

        assert len(gaps) == 1
        assert gaps[0].startswith("commit_history_unavailable:")

    @pytest.mark.parametrize("metadata", ["", "[project]\nname = 7\n", '[project]\nname = " "\n'])
    def test_commit_event_namespace_requires_a_project_name(
        self, tmp_path, monkeypatch, metadata
    ) -> None:
        project = tmp_path / "pyproject.toml"
        project.write_text(metadata, encoding="utf-8")
        monkeypatch.setattr(commits, "PROJECT", project)

        assert commits.commit_subject_gaps(tmp_path) == ["project_name_invalid"]

    def test_readme_install_path_matches_product_contract(self) -> None:
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

    def test_python_command_surfaces_use_one_modern_parser(self) -> None:
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

    def test_runtime_dependencies_are_exactly_pinned(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = pyproject["project"]["dependencies"]

        assert all(
            name and separator == "==" and version
            for name, separator, version in (
                requirement.partition("==") for requirement in dependencies
            )
        )

    def test_repository_cli_is_quiet_on_success_and_diagnostic_on_failure(
        self, mocker: MockerFixture
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

    def test_current_product_architecture_is_acyclic_and_directional(self) -> None:
        assert architecture_gaps(ROOT) == []

    def test_decision_records_have_one_register_and_semantic_names(self) -> None:
        assert decision_record_gaps(ROOT) == []

    def test_decision_record_gate_rejects_numeric_and_unregistered_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decisions = root / "docs/decisions"
            decisions.mkdir(parents=True)
            (decisions / "decision-register.md").write_text(
                "# Decision Records\n", encoding="utf-8"
            )
            (decisions / "0001-vague.md").write_text("# ADR-0001: Vague\n", encoding="utf-8")
            valid = decisions / "dr-0002-release-trust.md"
            valid.write_text(
                "# DR-0002: Release Trust\n\n"
                "- Status: accepted\n"
                "- Date: 2026-08-07\n\n"
                "## Context\n\nContext.\n\n"
                "## Decision\n\nDecision.\n\n"
                "## Consequences\n\nConsequences.\n\n"
                "## Revisit Trigger\n\nTrigger.\n",
                encoding="utf-8",
            )

            gaps = decision_record_gaps(root)

        assert "decision_record_name_invalid:docs/decisions/0001-vague.md" in gaps
        assert "decision_record_unregistered:docs/decisions/dr-0002-release-trust.md" in gaps

    def test_decision_record_gate_requires_unique_registration_without_history_ratchets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decisions = root / "docs/decisions"
            decisions.mkdir(parents=True)
            first = decisions / "dr-0001-boundary.md"
            third = decisions / "dr-0003-release.md"
            body = (
                "- Status: accepted\n"
                "- Date: 2026-08-07\n\n"
                "## Context\n\nContext.\n\n"
                "## Decision\n\nDecision.\n\n"
                "## Consequences\n\nConsequences.\n\n"
                "## Revisit Trigger\n\nTrigger.\n"
            )
            first.write_text("# DR-0001: Boundary\n\n" + body, encoding="utf-8")
            third.write_text("# DR-0003: Release\n\n" + body, encoding="utf-8")
            (decisions / "decision-register.md").write_text(
                "# Decision Records\n\n"
                "[DR-0001](dr-0001-boundary.md)\n"
                "[duplicate](dr-0001-boundary.md)\n"
                "[DR-0003](dr-0003-release.md)\n",
                encoding="utf-8",
            )

            gaps = decision_record_gaps(root)

        assert "decision_record_sequence_gap:0002" not in gaps
        assert "decision_record_registration_duplicate:docs/decisions/dr-0001-boundary.md" in gaps

    def test_tracked_project_files_follow_semantic_type_grammars(self) -> None:
        assert semantic_name_gaps(ROOT) == []

    def test_semantic_name_gate_rejects_numeric_and_cross_language_grammar(
        self,
    ) -> None:
        with _test_repository(
            (
                "src/valid_name.py",
                "src/invalid-name.py",
                "scripts/release/check_release.sh",
                "docs/2026-plan.md",
            )
        ) as root:
            gaps = semantic_name_gaps(root)

        assert "semantic_name_invalid:python:src/invalid-name.py" in gaps
        assert "semantic_name_invalid:shell:scripts/release/check_release.sh" in gaps
        assert "semantic_name_invalid:markdown:docs/2026-plan.md" in gaps

    def test_semantic_name_gate_exempts_only_openspec_history(self) -> None:
        with _test_repository(
            (
                "docs/2026-plan.md",
                "openspec/changes/archive/2026-08-07-release/specs/product/spec.md",
            )
        ) as root:
            gaps = semantic_name_gaps(root)

        assert gaps == ["semantic_name_invalid:markdown:docs/2026-plan.md"]

    def test_semantic_name_gate_accepts_official_pyinstaller_hook_modules(self) -> None:
        with _test_repository(("tools/release/hooks/hook-ctypes.py",)) as root:
            gaps = semantic_name_gaps(root)

        assert gaps == []

    def test_cli_is_the_only_production_command_composition_root(self) -> None:
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
                (
                    isinstance(node, ast.Import)
                    and any(alias.name == "argparse" for alias in node.names)
                )
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

    def test_lifecycle_tests_follow_lifecycle_ownership(self) -> None:
        tests = ROOT / "tests"
        assert [
            name for name in ("deployment", "payload", "supervision") if (tests / name).exists()
        ] == []
        assert (tests / "lifecycle/fixtures.py").is_file()
        assert (tests / "lifecycle/supervision/test_process.py").is_file()
        assert (tests / "serving/test_identity.py").is_file()

    def test_serving_tests_follow_runtime_and_deployment_ownership(self) -> None:
        tests = ROOT / "tests"
        assert [name for name in ("listener",) if (tests / name).exists()] == []
        assert (tests / "relay/fixtures.py").is_file()
        assert (tests / "runtime/test_process_environment.py").is_file()
        assert (tests / "serving/test_entrypoint.py").is_file()
        assert (tests / "serving/handoff/test_state_machine.py").is_file()
        assert (tests / "lifecycle/deployment/test_handoff.py").is_file()

    def test_protocol_and_relay_tests_follow_terminal_ownership(self) -> None:
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
            "relay/test_empty_response.py",
            "relay/test_routes.py",
        )
        assert all((tests / owner).is_file() for owner in owners)
