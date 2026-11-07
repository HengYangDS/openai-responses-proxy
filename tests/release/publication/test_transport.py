"""Hosted Forge subprocess transport contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tools.release.publication import hosted

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_hosted_transport_resolves_tools_and_translates_failures(*, mocker: MockerFixture) -> None:
    mocker.patch.object(hosted.shutil, "which", return_value=None)
    with pytest.raises(RuntimeError):
        hosted.executable("gh", RuntimeError)
    candidate = str(Path("native-tools", "gh").resolve())
    mocker.patch.object(hosted.shutil, "which", return_value=candidate)
    assert hosted.executable("gh", RuntimeError) == candidate

    completed = mocker.Mock(stdout='{"ok":true}')
    mocker.patch.object(hosted.subprocess, "run", return_value=completed)
    assert hosted.api_json(("gh", "api"), unavailable="offline", error_type=RuntimeError) == {
        "ok": True
    }
    bytes_completed = mocker.Mock(stdout=b"asset")
    mocker.patch.object(hosted.subprocess, "run", return_value=bytes_completed)
    assert (
        hosted.api_bytes(("gh", "api"), unavailable="offline", error_type=RuntimeError) == b"asset"
    )
    for failure in (OSError("missing"), subprocess.CalledProcessError(1, ["gh"])):
        mocker.patch.object(hosted.subprocess, "run", side_effect=failure)
        with pytest.raises(RuntimeError, match="offline"):
            hosted.api_json(("gh", "api"), unavailable="offline", error_type=RuntimeError)
        mocker.patch.object(hosted.subprocess, "run", side_effect=failure)
        with pytest.raises(RuntimeError, match="offline"):
            hosted.api_bytes(("gh", "api"), unavailable="offline", error_type=RuntimeError)
