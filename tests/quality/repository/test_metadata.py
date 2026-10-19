"""Native packaging and declared Python compatibility ownership."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.quality.repository import __main__ as repository_audit


class TestPackageMetadata:
    def test_repository_has_standard_package_metadata_and_one_version_owner(
        self,
    ) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]
        assert project["name"] == "openai-responses-proxy"
        assert project["requires-python"] == ">=3.12,<3.15"
        assert project["dynamic"] == ["version"]
        assert project["scripts"] == {
            "openai-responses-proxy": "openai_responses_proxy.cli.application:main"
        }
        assert metadata["build-system"]["build-backend"] == "hatchling.build"
        assert metadata["tool"]["hatch"]["version"]["path"] == "VERSION"
        assert "setuptools" not in metadata["tool"]
        assert "requires-python" not in metadata["tool"]["openai-responses-proxy"]
        assert "version-source" not in metadata["tool"]["openai-responses-proxy"]

    @pytest.mark.parametrize(
        ("requirement", "accepted"), [(">=3.12,<3.15", True), (">=3.12", False)]
    )
    def test_repository_audit_binds_python_support_to_its_matrix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requirement: str, accepted: bool
    ) -> None:
        source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = tomllib.loads(source)["project"]["requires-python"]
        project = tmp_path / "pyproject.toml"
        project.write_text(source.replace(declared, requirement), encoding="utf-8")
        monkeypatch.setattr(repository_audit, "PROJECT", project)
        gaps = repository_audit.audit()["policy_errors"]
        assert (not any(gap.startswith("requires_python_") for gap in gaps)) is accepted


def test_mit_license_is_present():
    license_text = Path(ROOT, "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
