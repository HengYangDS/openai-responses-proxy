"""Nox fixtures owned by the orchestration test domain."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nox.sessions import Session
from nox.virtualenv import VirtualEnv

if TYPE_CHECKING:
    from types import ModuleType

    from pytest_mock import MockerFixture


@pytest.fixture
def orchestration_session(tmp_path: Path, mocker: MockerFixture) -> Session:
    """Use native Nox environment semantics without launching external tools."""
    runner = mocker.Mock()
    runner.global_config.configure_mock(
        no_install=False,
        install_only=False,
        verbose=False,
        error_on_external_run=True,
        no_dependencies=False,
    )
    runner.envdir = str(tmp_path / "session")
    runner.venv = VirtualEnv(runner.envdir, interpreter=sys.executable, venv_backend="uv")
    runner.posargs = [str(tmp_path / "assets")]
    session = Session(runner)
    Path(session.bin).mkdir(parents=True)
    return session


@pytest.fixture(scope="module")
def nox_configuration() -> ModuleType:
    """Register the repository's Nox sessions once per test module."""
    return importlib.import_module("noxfile")
