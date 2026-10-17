"""Actual CUE journeys for the shared CI graph and its tracked projections."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.ci import project

MODEL = """package ci
import "strings"
#RuntimeMatrix: python: ["3.12", "3.13", "3.14"]
#PythonVersions: strings.Join(#RuntimeMatrix.python, "\\n")
#VerificationJobs: quality: session: "quality"
"""


@pytest.mark.repository_toolchain
def test_native_cue_projects_one_matrix_and_refuses_drift_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project.ROOT
    for relative in (
        ".config/mise/config.toml",
        ".config/mise/mise.lock",
        ".config/miserc.toml",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    model_root = tmp_path / ".config/ci"
    model = model_root / "graph/proof.cue"
    model.parent.mkdir(parents=True)
    model.write_text(MODEL, encoding="utf-8")
    for peer, expression in (("gitlab", "gitlab"), ("github", "githubVerify")):
        path = model_root / "peers" / peer / "verification.cue"
        path.parent.mkdir(parents=True)
        path.write_text(
            "package ci\n"
            f"{expression}: {{\nversions: #RuntimeMatrix.python\n"
            'quality: "nox -s " + #VerificationJobs.quality.session\n}\n',
            encoding="utf-8",
        )
    admission = model_root / "peers/github/admission.cue"
    admission.write_text(
        "package ci\ngithubAdmission: versions: #RuntimeMatrix.python\n", encoding="utf-8"
    )
    foreign = tmp_path / "operator.txt"
    foreign.write_bytes(b"preserve this independent file\n")
    monkeypatch.setattr(project, "ROOT", tmp_path)
    monkeypatch.setattr(project, "MODEL", model_root)
    monkeypatch.setattr(
        project,
        "PROJECTIONS",
        tuple(
            project.Projection(
                tmp_path / item.path.relative_to(root), item.expression, item.output_format
            )
            for item in project.PROJECTIONS
        ),
    )

    project.main(("--write",))
    versions = tmp_path / ".config/ci/python-versions"
    assert versions.read_bytes() == b"3.12\n3.13\n3.14\n"
    before = {item.path: item.path.read_bytes() for item in project.PROJECTIONS}
    project.main(())
    model.write_text(
        MODEL.replace(', "3.14"', "").replace('session: "quality"', 'session: "quick"'),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match=r"projection drift: .*\.config/ci/python-versions"):
        project.main(())
    assert {path: path.read_bytes() for path in before} == before

    project.main(("--write",))
    assert versions.read_bytes() == b"3.12\n3.13\n"
    for item in project.PROJECTIONS:
        if item.path.suffix == ".yml":
            projection = yaml.safe_load(item.path.read_bytes())
            assert projection["versions"] == ["3.12", "3.13"]
            if item.expression != "githubAdmission":
                assert projection["quality"] == "nox -s quick"
    accepted = {item.path: item.path.read_bytes() for item in project.PROJECTIONS}
    project.main(("--write",))
    project.main(())
    assert {path: path.read_bytes() for path in accepted} == accepted
    assert foreign.read_bytes() == b"preserve this independent file\n"


@pytest.mark.repository_toolchain
def test_production_graph_changes_reach_both_peer_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project.ROOT
    for relative in (
        ".config/mise/config.toml",
        ".config/mise/mise.lock",
        ".config/miserc.toml",
        *(str(Path(path).relative_to(root)) for path in project.model_inputs()),
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    monkeypatch.setattr(project, "ROOT", tmp_path)
    monkeypatch.setattr(project, "MODEL", tmp_path / ".config/ci")
    monkeypatch.setattr(
        project,
        "PROJECTIONS",
        tuple(
            project.Projection(
                tmp_path / item.path.relative_to(root), item.expression, item.output_format
            )
            for item in project.PROJECTIONS
        ),
    )
    project.main(("--write",))
    before = {item.path: item.path.read_bytes() for item in project.PROJECTIONS}
    assert before == {
        item.path: (root / item.path.relative_to(tmp_path)).read_bytes()
        for item in project.PROJECTIONS
    }
    model = tmp_path / ".config/ci/graph/proof.cue"
    model.write_text(model.read_text().replace('"quality"', '"quick"'), encoding="utf-8")
    assert project.reconcile(write=False) == (".gitlab-ci.yml", ".github/workflows/verify.yml")
    assert {path: path.read_bytes() for path in before} == before
    project.main(("--write",))
    github = yaml.safe_load((tmp_path / ".github/workflows/verify.yml").read_bytes())
    gitlab = yaml.safe_load((tmp_path / ".gitlab-ci.yml").read_bytes())
    assert any(
        "nox -s quick" in step.get("run", "") for step in github["jobs"]["python-quality"]["steps"]
    )
    for suffix in ("", "-accepted"):
        assert any(
            "nox -s quick" in command
            for command in gitlab[f"verify-python-quality{suffix}"]["script"]
        )
    assert project.reconcile(write=False) == ()


@pytest.mark.repository_toolchain
def test_native_cue_ignores_an_inherited_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISE_CONFIG_FILE", str(tmp_path / "foreign.toml"))
    value = project.render('strings.Join(#RuntimeMatrix.python, "\\n")', "text")
    assert value == (project.ROOT / ".config/ci/python-versions").read_bytes()


@pytest.mark.parametrize("initial", [None, b"stale\n", b"current\n"])
def test_ci_projection_checks_then_restores_only_owned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: bytes | None
) -> None:
    path = tmp_path / "nested" / "workflow.yml"
    foreign = tmp_path / "notes.txt"
    foreign.write_bytes(b"user owned\n")
    if initial is not None:
        path.parent.mkdir()
        path.write_bytes(initial)
    monkeypatch.setattr(project, "ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTIONS", (project.Projection(path, "workflow"),))
    monkeypatch.setattr(project, "render", lambda _expression, _format: b"current\n")

    if initial == b"current\n":
        project.main(())
    else:
        with pytest.raises(SystemExit, match=re.escape("projection drift: nested/workflow.yml")):
            project.main(())
    assert (path.read_bytes() if path.exists() else None) == initial
    project.main(("--write",))
    assert path.read_bytes() == b"current\n"
    project.main(())
    assert foreign.read_bytes() == b"user owned\n"
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob("*")} == {
        Path("notes.txt"),
        Path("nested"),
        Path("nested/workflow.yml"),
    }


@pytest.mark.parametrize("output_format", ["yaml", "text"])
def test_ci_projection_renderer_binds_the_locked_repository(
    mocker, monkeypatch: pytest.MonkeyPatch, output_format
) -> None:
    monkeypatch.setenv("MISE_CONFIG_FILE", "/foreign/config.toml")
    command = mocker.patch.object(
        project.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"yaml\n")
    )

    assert project.render("workflow", output_format) == b"yaml\n"

    args = command.call_args.args[0]
    assert args[:5] == ("mise", "exec", "--locked", "--", "cue")
    assert args[5:] == (
        "export",
        *project.model_inputs(),
        "--expression",
        "workflow",
        "--out",
        output_format,
    )
    assert command.call_args.kwargs["check"] is True
    assert command.call_args.kwargs["cwd"] == project.ROOT
    assert "MISE_CONFIG_FILE" not in command.call_args.kwargs["env"]
    assert command.call_args.kwargs["stdin"] == subprocess.DEVNULL
    command.side_effect = subprocess.CalledProcessError(1, args)
    with pytest.raises(subprocess.CalledProcessError):
        project.render("workflow")
