"""Contract tests for the repository-owned Python quality policy."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.quality.fixtures import ROOT


def _load_yaml(path: Path) -> dict[str, object]:
    """Load a workflow as semantic data rather than presentation text."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert all(isinstance(key, str) for key in data)
    return {str(key): value for key, value in data.items()}


def _mapping(value: object) -> dict[str, object]:
    """Narrow one parsed configuration table for semantic assertions."""
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _required_uv_version() -> str:
    metadata: object = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise TypeError("project metadata must be a table")
    tool = metadata.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    requirement: object = uv.get("required-version") if isinstance(uv, dict) else None
    if not isinstance(requirement, str) or not re.fullmatch(r"==\d+\.\d+\.\d+", requirement):
        raise AssertionError("uv must use an exact semantic version")
    return requirement.removeprefix("==")


class TestVerificationContracts:
    """Keep verification on mature tools and the released product artifact."""

    def test_pytest_is_the_only_behavior_test_runner(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        quality = metadata["dependency-groups"]["quality"]
        assert any(requirement.startswith("pytest==") for requirement in quality)
        assert any(requirement.startswith("pytest-mock==") for requirement in quality)
        pytest_config = tomllib.loads(
            (ROOT / ".config/checks/pytest/pytest.toml").read_text(encoding="utf-8")
        )["pytest"]
        assert pytest_config["addopts"] == ["--import-mode=importlib"]
        assert pytest_config["strict_config"] is True
        assert pytest_config["strict_markers"] is True
        assert pytest_config["cache_dir"] == ".cache/pytest"
        assert pytest_config["filterwarnings"] == ["error"]
        assert (
            "native_distribution: requires the self-contained released executable"
            in pytest_config["markers"]
        )
        assert pytest_config["python_classes"] == ["Test*", "*Tests", "*Contracts"]
        assert pytest_config["testpaths"] == ["tests"]
        direct_test_commands = []
        for relative in (
            ".gitlab-ci.yml",
            ".github/workflows/admission.yml",
            ".github/workflows/verify.yml",
            "noxfile.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), 1):
                if "python tests/" in line or '"-m", "tests.' in line:
                    direct_test_commands.append(f"{relative}:{lineno}:{line.strip()}")
        assert direct_test_commands == []
        gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        metadata_job = gitlab.split("verify-accepted-source:", 1)[1].split(
            "verify-release-tag:", 1
        )[0]
        assert "*install-uv" not in metadata_job
        assert (
            "uv sync --locked --group quality --python python --no-python-downloads" in metadata_job
        )
        assert "uv sync --locked --all-groups" not in metadata_job
        locked_python = "uv run --locked --no-sync --python python --no-python-downloads"
        assert "python tools/" not in metadata_job.replace(f"{locked_python} python tools/", "")
        assert f"{locked_python} python -m tools.release.metadata" in metadata_job
        assert f"{locked_python} python -m tools.quality.repository" in metadata_job
        assert "python -m pytest" not in metadata_job

    def test_uv_cache_writers_are_scoped_to_job_and_matrix_member(self) -> None:
        jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
        writers = []
        for job_value in jobs.values():
            steps = _mapping(job_value)["steps"]
            assert isinstance(steps, list)
            for step_value in steps:
                step = _mapping(step_value)
                if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                    writers.append(_mapping(step.get("with", {})))
        assert writers
        for settings in writers:
            assert settings.get("cache-suffix") == "${{ github.job }}-${{ strategy.job-index }}"

    def test_forge_bootstrap_derives_uv_requirement_from_project_metadata(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirement = metadata["tool"]["uv"]["required-version"]
        assert requirement.startswith("==")

        for relative in (".github/workflows/verify.yml", ".gitlab-ci.yml"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert f"uv{requirement}" not in source
            assert re.search(r"\buv==\d", source) is None

        github = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        assert "astral-sh/setup-uv@" in github
        assert "version:" not in "\n".join(
            line for line in github.splitlines() if "setup-uv" in line or "uv-version" in line
        )

        gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        uv_version = requirement.removeprefix("==")
        assert gitlab.count(f"ghcr.io/astral-sh/uv:{uv_version}-python") == 2
        pipeline = _load_yaml(ROOT / ".gitlab-ci.yml")
        job_scripts = {
            job: tuple(value.get("before_script", ()))
            for job, value in pipeline.items()
            if isinstance(value, dict) and "before_script" in value
        }
        assert job_scripts
        for scripts in job_scripts.values():
            if scripts[0] == "mise install --locked":
                assert scripts[:3] == (
                    "mise install --locked",
                    "npm ci --ignore-scripts",
                    "npm audit signatures",
                )
                continue
            native_check = "uv tree --frozen --offline --universal --depth 0"
            assert scripts.count(native_check) == 1
            assert scripts.index(native_check) < next(
                index for index, command in enumerate(scripts) if command == "apt-get update -qq"
            )
        assert "&install-uv" not in gitlab
        assert "*install-uv" not in gitlab
        assert "python -m pip install" not in gitlab

    @pytest.mark.parametrize(
        ("required_version", "expected_returncode"),
        [
            (_required_uv_version(), 0),
            ("9.9.9", 2),
        ],
    )
    def test_gitlab_uv_contract_uses_native_version_admission_without_mutation(
        self, tmp_path: Path, required_version: str, expected_returncode: int
    ) -> None:
        pipeline = _load_yaml(ROOT / ".gitlab-ci.yml")
        verify_python = _mapping(pipeline["verify-python"])
        before_script = verify_python["before_script"]
        assert isinstance(before_script, list)
        script = "uv tree --frozen --offline --universal --depth 0"
        assert script in before_script
        executable = shutil.which("uv")
        assert executable is not None
        source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            source.replace(
                f'required-version = "=={_required_uv_version()}"',
                f'required-version = "=={required_version}"',
            ),
            encoding="utf-8",
        )
        (tmp_path / "uv.lock").write_bytes((ROOT / "uv.lock").read_bytes())
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

        completed = subprocess.run(
            [executable, *shlex.split(script)[1:]],
            cwd=tmp_path,
            env={
                name: value
                for name, value in os.environ.items()
                if name not in {"UV_CONFIG_FILE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert completed.returncode == expected_returncode
        if expected_returncode:
            assert f"Required uv version `=={required_version}`" in completed.stderr
            assert "does not match the running version" in completed.stderr
        else:
            assert "openai-responses-proxy" in completed.stdout
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before

    def test_test_suite_has_no_unittest_compatibility_surface(self) -> None:
        offenders = []
        for path in sorted((ROOT / "tests").rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Import)
                    and any(
                        alias.name == "unittest" or alias.name.startswith("unittest.")
                        for alias in node.names
                    )
                ) or (
                    isinstance(node, ast.ImportFrom)
                    and (
                        node.module == "unittest"
                        or (node.module is not None and node.module.startswith("unittest."))
                    )
                ):
                    offenders.append(f"{relative}:{node.lineno}:unittest_import")
                elif isinstance(node, ast.ClassDef) and any(
                    (isinstance(base, ast.Name) and base.id == "TestCase")
                    or (
                        isinstance(base, ast.Attribute)
                        and isinstance(base.value, ast.Name)
                        and base.value.id == "unittest"
                        and base.attr == "TestCase"
                    )
                    for base in node.bases
                ):
                    offenders.append(f"{relative}:{node.lineno}:testcase_inheritance")
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "unittest"
                    and node.func.attr == "main"
                ):
                    offenders.append(f"{relative}:{node.lineno}:unittest_main")
                elif (
                    isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
                ):
                    offenders.append(f"{relative}:{node.lineno}:direct_test_entrypoint")
        assert offenders == []

    def test_native_distribution_tests_are_explicit_and_release_owned(self) -> None:
        markers = tomllib.loads(
            (ROOT / ".config/checks/pytest/pytest.toml").read_text(encoding="utf-8")
        )["pytest"]["markers"]
        assert "native_distribution: requires the self-contained released executable" in markers
        native_roots = (
            ROOT / "tests/serving/handoff/subprocess",
            ROOT / "tests/relay/native",
            ROOT / "tests/release/lifecycle",
        )
        carriers = []
        for root in native_roots:
            tests = sorted(root.glob("test_*.py"))
            assert tests, root
            carriers.extend(tests)
        for path in carriers:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            pytestmark = next(
                node
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "pytestmark"
                    for target in node.targets
                )
            )
            assert isinstance(pytestmark.value, ast.List)
            marks = {ast.unparse(element) for element in pytestmark.value.elts}
            assert not any("skipif" in mark for mark in marks)
            assert "pytest.skip" not in source
            assert "pytest.mark.native_distribution" in marks
            assert "pytest.mark.usefixtures(preserve_native_host_projection.__name__)" in marks
        cleanup = (ROOT / "tests/serving/handoff/test_cleanup.py").read_text(encoding="utf-8")
        assert "pytest.mark.native_distribution" not in cleanup
        assert "preserve_native_host_projection" not in cleanup

    def test_quality_environment_is_repository_owned_and_locked(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = metadata["dependency-groups"]["quality"]
        names = [requirement.partition("==")[0] for requirement in requirements]
        assert requirements
        assert len(names) == len(set(names))
        assert all(
            name and separator == "==" and version
            for name, separator, version in (
                requirement.partition("==") for requirement in requirements
            )
        )
        assert re.fullmatch(r"==\d+\.\d+\.\d+", metadata["tool"]["uv"]["required-version"])
        assert metadata["tool"]["uv"]["link-mode"] == "copy"
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        resolved = {package["name"]: package.get("version") for package in lock["package"]}
        assert all(
            resolved[name] == version
            for name, _, version in (requirement.partition("==") for requirement in requirements)
        )

        toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text(encoding="utf-8"))
        assert toolchain["settings"] == {
            "idiomatic_version_file_enable_tools": [],
            "locked": True,
            "not_found_system_fallback": False,
            "use_versions_host": False,
        }
        early = tomllib.loads((ROOT / ".config/miserc.toml").read_text(encoding="utf-8"))
        assert early["ceiling_paths"] == ["{{ config_root | dirname | dirname }}"]
        assert len(early["ignored_config_paths"]) == 3

    def test_native_release_tools_are_isolated_from_quality(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        groups = metadata["dependency-groups"]
        quality = {requirement.partition("==")[0] for requirement in groups["quality"]}
        release = {requirement.partition("==")[0] for requirement in groups["release"]}

        assert quality.isdisjoint(release)
        assert release == {"pyinstaller"}

    def test_developer_bootstrap_installs_product_and_quality_groups(self) -> None:
        toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text(encoding="utf-8"))
        environment = toolchain["env"]
        assert environment["UV_PROJECT_ENVIRONMENT"] == "{{config_root}}/.venv"
        assert environment["VIRTUAL_ENV"] is False
        assert environment["UV_PYTHON"] is False
        assert environment["PYTHONHOME"] is False
        assert environment["PYTHONPATH"] is False
        assert environment["PYTHONNOUSERSITE"] == "1"
        assert environment["NPM_CONFIG_USERCONFIG"] == "{{ config_root }}/.config/npm/npmrc"
        assert not (ROOT / ".npmrc").exists()
        assert not (ROOT / "mise.toml").exists()
        assert toolchain["tasks"]["bootstrap"]["run"] == [
            'uv sync --locked --all-groups --python "{{tools.python.path}}"',
            "npm ci --ignore-scripts",
            "npm audit signatures",
            "mise run workflow-supply",
        ]
        for relative in ("AGENTS.md", "CONTRIBUTING.md", "README.md"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert "mise run bootstrap" in source
            assert "mise run check" in source

    @pytest.mark.parametrize(
        ("task", "session"),
        [
            ("quick", "quick"),
            ("fix", "fix"),
            ("check", "full"),
            ("native", "release"),
            ("release", "release_asset"),
        ],
    )
    def test_developer_tasks_delegate_to_existing_verification(
        self, task: str, session: str
    ) -> None:
        toolchain = tomllib.loads((ROOT / ".config/mise/config.toml").read_text(encoding="utf-8"))
        assert toolchain["tasks"][task]["run"] == (
            'uv run --locked --no-sync --python "{{tools.python.path}}" nox -s ' + session
        )

    @pytest.mark.parametrize(
        ("relative", "source", "expected"),
        [
            (
                "src/openai_responses_proxy/probe.py",
                "def answer():\n    return 42\n",
                {"D100", "D103", "ANN201"},
            ),
            ("tools/probe.py", "def answer():\n    return 42\n", {"D100", "D103", "ANN201"}),
            ("noxfile.py", "def answer():\n    return 42\n", {"D100", "D103", "ANN201"}),
            (
                "src/openai_responses_proxy/probe.py",
                '"""Native annotation probe."""\n'
                "def answer() -> int:\n"
                '    """Return the answer."""\n    return 42\n',
                set(),
            ),
            (
                "tools/probe.py",
                '"""Native annotation probe."""\n'
                "class Input:\n"
                '    """One public input."""\n'
                "    def __init__(self, value: int):\n"
                '        """Bind the selected value."""\n        self.value = value\n',
                {"ANN204"},
            ),
            ("tests/probe.py", "def answer():\n    return 42\n", set()),
            (
                "tests/probe.py",
                'def answer():\n    """Return the answer"""\n    return 42\n',
                {"D415"},
            ),
        ],
    )
    def test_native_ruff_applies_public_interface_policy_by_semantic_role(
        self, relative: str, source: str, expected: set[str]
    ) -> None:
        result = subprocess.run(
            (
                "ruff",
                "check",
                "--config",
                str(ROOT / ".config/checks/ruff/ruff.toml"),
                "--output-format",
                "json",
                "--stdin-filename",
                str(ROOT / relative),
                "-",
            ),
            input=source,
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == bool(expected), result.stderr
        assert {item["code"] for item in json.loads(result.stdout)} == expected

    def test_repository_declares_the_supported_python_matrix_once(self) -> None:
        github = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        supported = (ROOT / ".config/ci/python-versions").read_text(encoding="utf-8").splitlines()
        uv_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
            "uv"
        ]["required-version"].removeprefix("==")
        image_pattern = re.compile(
            r"^ghcr\.io/astral-sh/uv:(?P<uv>\d+\.\d+\.\d+)-python"
            r"(?P<minor>\d+\.\d+)-trixie-slim"
            r"@sha256:(?P<digest>[0-9a-f]{64})$"
        )
        image_values = dict(
            re.findall(r"^  (UV_PYTHON_(?:FLOOR|LATEST)_IMAGE): (\S+)$", gitlab, re.MULTILINE)
        )
        assert set(image_values) == {"UV_PYTHON_FLOOR_IMAGE", "UV_PYTHON_LATEST_IMAGE"}
        floor_image = image_pattern.fullmatch(image_values["UV_PYTHON_FLOOR_IMAGE"])
        latest_image = image_pattern.fullmatch(image_values["UV_PYTHON_LATEST_IMAGE"])
        assert floor_image is not None
        assert latest_image is not None
        assert floor_image.group("uv") == uv_version
        assert latest_image.group("uv") == uv_version
        assert floor_image.group("minor") == supported[0]
        assert latest_image.group("minor") == supported[-1]
        assert "python -m tools.quality.python_matrix" in github
        assert 'print(f"floor={versions[0]}"' not in github
        assert 'print(f"latest={versions[-1]}"' not in github
        assert "needs.python-matrix.outputs.floor" in github
        assert "needs.python-matrix.outputs.latest" in github
        assert "needs.python-matrix.outputs.release" in github
        assert "python-version: ${{ needs.python-matrix.outputs.release }}" in github
        pipeline = _load_yaml(ROOT / ".gitlab-ci.yml")
        assert _mapping(pipeline["default"])["image"] == {"name": "$UV_PYTHON_LATEST_IMAGE"}
        assert _mapping(pipeline["verify-python-quality"])["image"] == {
            "name": "$UV_PYTHON_FLOOR_IMAGE"
        }
        assert "LINUX_RELEASE_IMAGE" not in gitlab

    def test_release_collects_ctypes_as_source_outside_the_pyz_archive(self) -> None:
        """Avoid marshal identity drift in the Python 3.14 ``ctypes`` code object."""
        hook = ROOT / "tools" / "release" / "hooks" / "hook-ctypes.py"

        hook_tree = ast.parse(hook.read_text(encoding="utf-8"))
        executable_statements = hook_tree.body[1:]

        assert ast.get_docstring(hook_tree)
        assert len(executable_statements) == 1
        assignment = executable_statements[0]
        assert isinstance(assignment, ast.Assign)
        assert [target.id for target in assignment.targets if isinstance(target, ast.Name)] == [
            "module_collection_mode"
        ]
        assert ast.literal_eval(assignment.value) == "py"

    def test_forge_quality_jobs_use_the_locked_runner(self) -> None:
        github = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
        gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        quality_job = github.split("  python-quality:", 1)[1].split("  native-assets:", 1)[0]
        assert "uv run --locked --group quality nox -s quality" in quality_job
        assert "uv sync --locked --only-group quality" not in quality_job
        assert "uv sync --locked --group quality --python python --no-python-downloads" in gitlab
        assert (
            "uv run --locked --no-sync --python python --no-python-downloads nox -s quality"
            in gitlab
        )
        assert "fetch-depth: 0" in quality_job
        assert "fetch-tags: true" in quality_job
        assert "python -m tools.quality.repository" not in quality_job
        assert "uv run --locked --group quality nox -s quality" in github
        assert "python -m tools.quality.repository" in gitlab

    def test_hosted_governance_tools_use_the_complete_locked_environment(self) -> None:
        github = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
        gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        source = gitlab.split("source-and-governance:", 1)[1].split("verify-python:", 1)[0]
        assert "uv sync --locked --group quality --python python --no-python-downloads" in source
        assert "uv sync --locked --all-groups" in github

    def test_performance_is_an_independent_locked_proof_surface(self) -> None:
        """Performance must be measured explicitly, not inferred from functional tests."""
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        quality = metadata["dependency-groups"]["quality"]
        assert "pyperf==2.10.0" in quality
        assert (ROOT / ".config/checks/performance/policy.toml").is_file()
        assert (ROOT / "tools/performance/benchmark.py").is_file()
        assert (ROOT / "tools/performance/memory.py").is_file()

        github = _load_yaml(ROOT / ".github/workflows/verify.yml")
        gitlab = _load_yaml(ROOT / ".gitlab-ci.yml")
        github_jobs = _mapping(github["jobs"])
        assert "performance" in github_jobs
        assert "uv run --locked --group quality nox -s performance" in str(
            github_jobs["performance"]
        )
        assert "verify-performance" in gitlab
        assert "nox -s performance" in str(gitlab["verify-performance"])
