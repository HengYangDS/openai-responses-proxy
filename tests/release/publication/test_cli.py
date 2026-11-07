"""Secret-free rendering and exit status for publication verification."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest

from tests.release.publication.fixtures import VERIFY_ARGUMENTS
from tools.release.publication import __main__ as publication_cli
from tools.release.publication import verification as publication

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class PublicationProofCliContracts:
    """Render deeply frozen evidence without adding release authority."""

    @pytest.mark.parametrize("as_json", [True, False])
    def test_cli_serializes_frozen_publication_evidence(
        self, capsys: pytest.CaptureFixture[str], *, as_json: bool, mocker: MockerFixture
    ) -> None:
        frozen = MappingProxyType(
            {
                "verified": True,
                "forges": MappingProxyType(
                    {"github": MappingProxyType({"ci": MappingProxyType({"id": 1})})}
                ),
            }
        )

        verify = mocker.patch.object(publication, "verify", return_value=frozen)
        arguments = (
            "verify",
            *(
                item
                for key, value in VERIFY_ARGUMENTS.items()
                for item in (f"--{key.replace('_', '-')}", str(value))
            ),
        )
        publication_cli.main((*arguments, "--json") if as_json else arguments)

        verify.assert_called_once_with(**VERIFY_ARGUMENTS)
        output = capsys.readouterr().out
        if as_json:
            assert json.loads(output) == {
                "verified": True,
                "forges": {"github": {"ci": {"id": 1}}},
            }
        else:
            assert output == "publication proof: VERIFIED\n"

    def test_cli_reports_stable_semantic_failure_reason(
        self, capsys: pytest.CaptureFixture[str], *, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            publication,
            "verify",
            side_effect=publication.PublicationError(
                "live publication evidence is unavailable or invalid",
                reasons=("gitlab.hosted_evidence_invalid",),
            ),
        )

        with pytest.raises(SystemExit) as failure:
            publication_cli._verify(
                tag="v1.2.3",
                gitlab_git_url="https://gitlab.example/team/repository.git",
                gitlab_api_base="https://gitlab.example/api/v4",
                gitlab_repo="team/repository",
                github_git_url="https://github.example/team/repository.git",
                github_repo="team/repository",
                gitlab_anchor=Path("gitlab-anchor"),
                github_anchor=Path("github-anchor"),
                as_json=True,
            )

        assert failure.value.code == 1
        assert '"reasons":["gitlab.hosted_evidence_invalid"]' in capsys.readouterr().out

    def test_cli_invalid_tag_fails_without_echoing_secret_environment(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.release.publication", "verify", "--tag", "latest"],
            capture_output=True,
            check=False,
            text=True,
            env={"PATH": "/usr/bin:/bin", "SECRET_TOKEN": "do-not-print"},
        )
        assert completed.returncode == 2
        assert "do-not-print" not in completed.stdout + completed.stderr
