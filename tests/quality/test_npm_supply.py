"""The npm that runs is the one that the tool configuration selects."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from tools.quality import npm_supply
from tools.quality.release_age import DECLARATION

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def _declare(root: Path, tools: str) -> None:
    path = root / DECLARATION
    path.parent.mkdir(parents=True)
    path.write_text(f"[tools]\n{tools}", encoding="utf-8")


def _npm_prints(mocker: MockerFixture, version: str) -> None:
    mocker.patch.object(npm_supply.shutil, "which", return_value="/bin/npm")
    mocker.patch.object(
        npm_supply.subprocess,
        "run",
        return_value=subprocess.CompletedProcess(("npm", "--version"), 0, f"{version}\n", ""),
    )


def test_the_selected_npm_passes(tmp_path: Path, mocker: MockerFixture) -> None:
    _declare(tmp_path, 'npm = "1.2.3"\n')
    _npm_prints(mocker, "1.2.3")

    npm_supply.audit(tmp_path)


def test_another_npm_is_refused_naming_both(tmp_path: Path, mocker: MockerFixture) -> None:
    _declare(tmp_path, 'npm = "1.2.3"\n')
    _npm_prints(mocker, "1.2.4")

    with pytest.raises(npm_supply.NpmSupplyError, match=r"npm 1\.2\.4 runs.*selects npm 1\.2\.3"):
        npm_supply.audit(tmp_path)


@pytest.mark.parametrize("tools", ['node = "1.0.0"\n', 'npm = ""\n', "npm = 1\n"])
def test_a_configuration_without_npm_is_refused(tools: str, tmp_path: Path) -> None:
    _declare(tmp_path, tools)

    with pytest.raises(npm_supply.NpmSupplyError, match="npm_unselected"):
        npm_supply.selected(tmp_path)


@pytest.mark.parametrize(
    "failure",
    [
        OSError("missing"),
        subprocess.CalledProcessError(1, "npm"),
        subprocess.TimeoutExpired("npm", 60),
    ],
)
def test_an_npm_that_does_not_run_is_refused(failure: Exception, mocker: MockerFixture) -> None:
    mocker.patch.object(npm_supply.shutil, "which", return_value="/bin/npm")
    mocker.patch.object(npm_supply.subprocess, "run", side_effect=failure)

    with pytest.raises(npm_supply.NpmSupplyError, match="npm_unavailable"):
        npm_supply.running()


def test_no_npm_on_path_is_refused(mocker: MockerFixture) -> None:
    mocker.patch.object(npm_supply.shutil, "which", return_value=None)

    with pytest.raises(npm_supply.NpmSupplyError, match="no npm on PATH"):
        npm_supply.running()


def test_the_command_reports_a_refusal_and_exits_one(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(tmp_path, 'npm = "1.2.3"\n')
    _npm_prints(mocker, "1.2.4")
    mocker.patch.object(npm_supply, "ROOT", tmp_path)

    with pytest.raises(SystemExit) as stopped:
        npm_supply.main()

    assert stopped.value.code == 1
    assert "npm_not_selected" in capsys.readouterr().err


def test_the_command_is_silent_when_npm_is_selected(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _declare(tmp_path, 'npm = "1.2.3"\n')
    _npm_prints(mocker, "1.2.3")
    mocker.patch.object(npm_supply, "ROOT", tmp_path)

    npm_supply.main()

    assert capsys.readouterr() == ("", "")
