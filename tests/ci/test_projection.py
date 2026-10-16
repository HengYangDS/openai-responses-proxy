"""Actual CUE journeys for the shared CI graph and its tracked projections."""

from pathlib import Path

import pytest
import yaml

from tools.ci import project

MODEL = """package ci
import "strings"
#RuntimeMatrix: python: ["3.12", "3.13", "3.14"]
#PythonVersions: strings.Join(#RuntimeMatrix.python, "\\n")
gitlab: versions: #RuntimeMatrix.python
githubAdmission: versions: #RuntimeMatrix.python
githubVerify: versions: #RuntimeMatrix.python
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
    model = tmp_path / ".config/ci/pipeline.cue"
    model.parent.mkdir()
    model.write_text(MODEL, encoding="utf-8")
    foreign = tmp_path / "operator.txt"
    foreign.write_bytes(b"preserve this independent file\n")
    monkeypatch.setattr(project, "ROOT", tmp_path)
    monkeypatch.setattr(project, "MODEL", model)
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
    versions = tmp_path / ".python-versions"
    assert versions.read_bytes() == b"3.12\n3.13\n3.14\n"
    before = {item.path: item.path.read_bytes() for item in project.PROJECTIONS}
    project.main(())
    model.write_text(MODEL.replace(', "3.14"', ""), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"projection drift: .*\.python-versions"):
        project.main(())
    assert {path: path.read_bytes() for path in before} == before

    project.main(("--write",))
    assert versions.read_bytes() == b"3.12\n3.13\n"
    for item in project.PROJECTIONS:
        if item.path.suffix == ".yml":
            assert yaml.safe_load(item.path.read_bytes())["versions"] == ["3.12", "3.13"]
    accepted = {item.path: item.path.read_bytes() for item in project.PROJECTIONS}
    project.main(("--write",))
    project.main(())
    assert {path: path.read_bytes() for path in accepted} == accepted
    assert foreign.read_bytes() == b"preserve this independent file\n"


@pytest.mark.repository_toolchain
def test_native_cue_ignores_an_inherited_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISE_CONFIG_FILE", str(tmp_path / "foreign.toml"))
    value = project.render('strings.Join(#RuntimeMatrix.python, "\\n")', "text")
    assert value == (project.ROOT / ".python-versions").read_bytes()
