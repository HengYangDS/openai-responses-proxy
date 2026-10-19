"""Shared verification graph, Python compatibility and syntax contracts."""

import re

import pytest

from tests.forge.workflows.fixtures import _load_yaml
from tests.forge.workflows.fixtures import _mapping
from tests.forge.workflows.fixtures import _sequence
from tests.forge.workflows.fixtures import _string
from tests.quality.fixtures import ROOT
from tools.ci import project
from tools.ci.project import MODEL
from tools.ci.project import reconcile

GITLAB_LOCKED_PYTHON = "uv run --locked --no-sync --python python --no-python-downloads"


@pytest.mark.repository_toolchain
def test_forge_workflows_are_generated_from_one_declarative_graph() -> None:
    """Keep Forge syntax as projections, never independent topology owners."""
    governance = (ROOT / "tools" / "quality" / "governance.py").read_text(encoding="utf-8")
    assert 'sys.executable, "-m", "tools.ci.project"' in governance
    assert MODEL == ROOT / ".config/ci"
    assert reconcile(write=False) == ()


@pytest.mark.parametrize("expression", [item.expression for item in project.PROJECTIONS])
def test_projection_command_refuses_drift_and_writes_only_declared_outputs(
    tmp_path, mocker, expression
) -> None:
    projections = tuple(
        project.Projection(
            tmp_path / item.path.relative_to(ROOT), item.expression, item.output_format
        )
        for item in project.PROJECTIONS
    )
    rendered = {item.expression: b"generated output\n" for item in projections}
    mocker.patch.object(project, "ROOT", tmp_path)
    mocker.patch.object(project, "PROJECTIONS", projections)
    mocker.patch.object(project, "render", side_effect=lambda name, _format: rendered[name])
    for item in projections:
        item.path.parent.mkdir(parents=True, exist_ok=True)
        item.path.write_bytes(rendered[item.expression])
    changed = next(item for item in projections if item.expression == expression)
    changed.path.write_bytes(b"manual edit\n")
    unrelated = tmp_path / "operator-owned.txt"
    unrelated.write_bytes(b"preserved\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(
        SystemExit,
        match="projection drift: " + re.escape(changed.path.relative_to(tmp_path).as_posix()),
    ):
        project.main(())
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before

    project.main(("--write",))
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == {**before, changed.path: rendered[expression]}
    project.main(())
    project.main(("--write",))
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == after


def _assert_github_required_tokens(text: str) -> None:
    required = [
        "name: Verify",
        "push:",
        "permissions:\n  contents: read",
        'GIT_CONFIG_COUNT: "1"',
        "GIT_CONFIG_KEY_0: init.defaultBranch",
        "GIT_CONFIG_VALUE_0: main",
        "runs-on: macos-26",
        "runs-on: ubuntu-24.04",
        "actions/checkout@",
        "uv run --locked --no-sync python -m tools.quality.python_matrix",
        "python-version: ${{ fromJSON(needs.python-matrix.outputs.versions) }}",
        'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"',
        "python-windows:",
        "windows-2025",
        "actions/setup-python@",
        "fetch-tags: true",
        "if: github.event_name == 'push' && github.ref_type == 'tag'",
        "python -m tools.quality.governance --online-links",
        'uv run --locked --no-sync python -m tools.release.metadata --tag "$GITHUB_REF_NAME"',
        "uv run --locked --no-sync python -m tools.release.metadata",
        "python-quality:",
        "astral-sh/setup-uv@",
        "uv run --locked --group quality nox -s quality",
        "python -m pytest -q --rootdir . -c .config/checks/pytest/pytest.toml "
        "tests/quality/test_policy.py tests/quality/repository tests/quality/orchestration/test_governance.py tests/forge/workflows",
        "tests/release/publication",
        "native-assets:",
        "name: Native asset (${{ matrix.platform }})",
        "platform: macos-arm64",
        "platform: windows-x86_64",
        "native-linux:",
        "name: Native asset (linux-x86_64)",
        "container: ${{ needs.python-matrix.outputs.linux-release-image }}",
        'uv run --locked --no-sync nox -s release -- "${{ runner.temp }}/native-assets"',
        "actions/upload-artifact@",
        "release-assets:",
        "name: Release assets",
        "python-version: ${{ needs.python-matrix.outputs.latest }}",
        "name: Download native release assets",
        "GH_TOKEN: ${{ github.token }}",
        """gh run download "$GITHUB_RUN_ID" --pattern 'native-*' """
        """--dir "$RUNNER_TEMP/native" """.rstrip(),
        "uv run --locked --no-sync python -m tools.release.artifact assemble",
        "CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY",
        "OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST",
        'install -m 600 /dev/null "$RUNNER_TEMP/release-asset-signing-key"',
        'printf \'%s\\n\' "$RELEASE_ASSET_SIGNING_KEY_TEXT" > "$RUNNER_TEMP/release-asset-signing-key"',
        "RELEASE_ASSET_SIGNING_KEY_PATH: ${{ runner.temp }}/release-asset-signing-key",
        "--sign",
        "name: release-assets",
    ]
    for token in required:
        if token not in text:
            raise AssertionError(f"GitHub Actions verification contract is missing {token!r}")
    if "contents: write" in text:
        raise AssertionError("verification workflow must use read-only repository permissions")


def _assert_github_matrix_contract(text: str) -> None:
    matrix_start = text.index("\n  python-matrix:")
    matrix_end = text.index("\n  python:", matrix_start)
    matrix_block = text[matrix_start:matrix_end]
    for token in (
        "astral-sh/setup-uv@",
        "uv sync --locked --all-groups",
        "uv run --locked --no-sync python -m tools.quality.python_matrix",
    ):
        if token not in matrix_block:
            raise AssertionError(
                f"Python matrix bootstrap must install and use the locked environment: {token!r}"
            )
    if "needs.native-assets.outputs.python-version" in text:
        raise AssertionError(
            "release assembly must read the Python SSOT, not a pass-through job output"
        )
    if (
        "RELEASE_ASSET_SIGNING_KEY_PATH: "
        "${{ secrets.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY }}"
    ) in text:
        raise AssertionError("the product signer must receive a key path, not secret text")
    if "pull_request_target:" in text:
        raise AssertionError("verification workflow must not execute privileged pull-request code")
    if "@main" in text or "@master" in text:
        raise AssertionError("GitHub Actions must use immutable action revisions")


def _assert_github_platform_contract(text: str) -> None:
    mac_start = text.index("\n  python:")
    windows_start = text.index("\n  python-windows:")
    governance_start = text.index("\n  accepted-source:")
    mac_block = text[mac_start:windows_start]
    windows_block = text[windows_start:governance_start]
    setup_uv = "astral-sh/setup-uv@"
    if mac_block.count(setup_uv) != 1:
        raise AssertionError("macOS Python matrix must use exactly one setup-uv action")
    test_owner = 'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"'
    if test_owner not in mac_block:
        raise AssertionError(f"macOS Python matrix must run {test_owner}")
    for token in (
        "runs-on: windows-2025",
        "python-version: ${{ fromJSON(needs.python-matrix.outputs.versions) }}",
        "actions/checkout@",
        "actions/setup-python@",
        'uv run --locked --group quality nox -s "tests-${{ matrix.python-version }}"',
    ):
        if token not in windows_block:
            raise AssertionError(f"Windows Python matrix must contain {token!r}")
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    host_native_jobs = (
        "source-and-governance",
        "python",
        "python-windows",
        "accepted-source",
        "promotion",
        "tag-metadata",
        "python-quality",
        "native-assets",
        "release-assets",
        "published-release-compatibility",
    )
    for job_id in host_native_jobs:
        steps = _sequence(_mapping(jobs[job_id])["steps"])
        assert any(
            _string(_mapping(step).get("uses", "")).startswith("actions/setup-python@")
            for step in steps
        ), f"{job_id} must use pinned setup-python"
    if windows_block.count("actions/setup-python@") != 1:
        raise AssertionError("Windows verification must use exactly one pinned setup-python action")
    if windows_block.count(setup_uv) != 1:
        raise AssertionError("Windows Python matrix must use exactly one setup-uv action")
    quality_start = text.index("\n  python-quality:")
    quality_end = text.index("\n  native-assets:", quality_start)
    quality_block = text[quality_start:quality_end]
    for token in ("fetch-depth: 0", "fetch-tags: true"):
        if token not in quality_block:
            raise AssertionError(f"quality checkout must contain {token!r}")
    for patch_pin in ("3.12.", "3.13.", "3.14."):
        if patch_pin in text:
            raise AssertionError(
                f"GitHub workflows must select supported Python lines, not patch releases: {patch_pin!r}"
            )


def _assert_github_governance_contract(text: str) -> None:
    accepted_start = text.index("\n  accepted-source:")
    tag_start = text.index("\n  tag-metadata:")
    quality_start = text.index("\n  python-quality:", tag_start)
    accepted_block = text[accepted_start:tag_start]
    tag_block = text[tag_start:quality_start]
    for block in (accepted_block, tag_block):
        for token in ("fetch-depth: 0", "fetch-tags: true"):
            if token not in block:
                raise AssertionError(f"governance checkout must contain {token!r}")
    tag_check = (
        'uv run --locked --no-sync python -m tools.release.metadata --tag "$GITHUB_REF_NAME"'
    )
    branch_check = "uv run --locked --no-sync python -m tools.release.metadata"
    if tag_check not in tag_block:
        raise AssertionError("tag metadata must validate the exact annotated tag")
    if branch_check not in accepted_block:
        raise AssertionError("accepted source must validate mainline metadata")
    if "--prepare-release" in accepted_block:
        raise AssertionError("accepted source verification must not require release preparation")


def _assert_github_native_and_forbidden_contract(text: str) -> None:
    windows_start = text.index("\n  python-windows:")
    governance_start = text.index("\n  accepted-source:")
    windows_block = text[windows_start:governance_start]
    jobs = _mapping(_load_yaml(ROOT / ".github/workflows/verify.yml")["jobs"])
    for job_id in ("native-assets", "native-linux"):
        for step in _sequence(_mapping(jobs[job_id])["steps"]):
            native_step = _mapping(step)
            assert "shell" not in native_step
            assert "set -euo pipefail" not in _string(native_step.get("run", ""))
    if "shell:" in windows_block or re.search(r"(?:^|\s)\S+\.sh(?:\s|$)", windows_block):
        raise AssertionError("Windows verification must not depend on Bash or POSIX shell scripts")
    if "secrets:" in windows_block or "permissions:" in windows_block:
        raise AssertionError(
            "Windows verification must inherit the read-only, secret-free workflow contract"
        )
    for forbidden in (
        "self-hosted",
        "openai-responses-proxy-github-macos-arm64",
        "/opt/homebrew",
        "refs/openai-responses-proxy/runner-checkout-retained",
        "git update-ref",
    ):
        if forbidden in text:
            raise AssertionError(
                f"GitHub workflows must not depend on runner-local state: {forbidden!r}"
            )
    for forbidden in ("git config --global", "GIT_ADVICE", "advice.detachedHead"):
        if forbidden in text:
            raise AssertionError(
                f"GitHub workflows must not suppress Git diagnostics with {forbidden!r}"
            )
    print("GitHub Actions verification contract: OK")


def test_github_verification_workflow_contract() -> None:
    text = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    _assert_github_required_tokens(text)
    _assert_github_matrix_contract(text)
    _assert_github_platform_contract(text)
    _assert_github_governance_contract(text)
    _assert_github_native_and_forbidden_contract(text)
    source = text.split("\n  source-and-governance:", 1)[1].split("\n  python:", 1)[0]
    assert source.count("python -m tools.quality.governance --online-links") == 1
    for duplicate in (
        "cue vet",
        "openspec validate --all",
        "actionlint .github/workflows",
        "gitleaks git",
        "python -m tools.release.metadata",
        "python -m tools.quality.repository",
    ):
        assert duplicate not in source


def test_gitlab_pytest_invocations_preserve_repository_module_resolution() -> None:
    text = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")

    if f"{GITLAB_LOCKED_PYTHON} pytest" in text:
        raise AssertionError("GitLab must not invoke the pytest console script directly")
    assert f"{GITLAB_LOCKED_PYTHON} pytest" not in text
