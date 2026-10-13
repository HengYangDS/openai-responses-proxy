"""Exclusive lifecycle mutation with independent read-only observation."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest import mock

import pytest
from filelock import FileLock

from openai_responses_proxy import errors
from openai_responses_proxy.cli import application
from openai_responses_proxy.runtime import config
from tests.cli.fixtures import invoke
from tests.lifecycle.fixtures import install_context


@pytest.mark.parametrize(
    ("os_name", "system"),
    [("posix", "linux"), ("posix", "darwin"), ("nt", "win32")],
)
def test_lifecycle_lock_leaves_a_fresh_default_installation_root_absent(
    tmp_path: Path,
    os_name: str,
    system: str,
    *,
    mocker,
) -> None:
    """Take the writer lock outside the default installation root on every platform."""
    home = tmp_path / "home"
    environment = {"HOME": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local")}
    mocker.patch.object(config, "home_dir", return_value=str(home))
    with (
        mock.patch.dict(config.os.environ, environment, clear=True),
        mock.patch.object(config.os, "name", os_name),
        mock.patch.object(config.sys, "platform", system),
    ):
        roots = config.data_dir(), config.state_dir()
    install_root, state_root = map(Path, roots)
    ctx = dataclasses.replace(
        install_context(tmp_path), install_dir=str(install_root), log_dir=str(state_root)
    )
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    observed: list[bool] = []
    mocker.patch.object(
        application.install,
        "install_asset",
        side_effect=lambda *_args, **_kwargs: observed.append(install_root.exists()),
    )

    application.dispatch(
        "install",
        port=ctx.port,
        asset=tmp_path / "release.tar.gz",
        trust_anchor=tmp_path / "trust",
    )

    assert observed == [False]
    assert (state_root / "lifecycle.lock").is_file()


@pytest.mark.parametrize("command", ["install", "recover", "reload", "rollback", "uninstall"])
def test_mutating_commands_reject_a_second_lifecycle_writer(
    tmp_path: Path,
    command: str,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    owners = {
        "install": mocker.patch.object(application.install, "install_asset"),
        "recover": mocker.patch.object(application.control, "recover"),
        "reload": mocker.patch.object(application.control, "reload"),
        "rollback": mocker.patch.object(application.control, "rollback"),
        "uninstall": mocker.patch.object(application.uninstall, "uninstall_product"),
    }
    arguments: dict[str, object] = {"port": ctx.port}
    if command == "install":
        arguments.update(asset=tmp_path / "release.tar.gz", trust_anchor=tmp_path / "trust")
    elif command == "rollback":
        arguments["to_release"] = "3.1.13"

    with (
        FileLock(
            application._lifecycle_lock_path(ctx),
            timeout=0,
            fallback_to_soft=False,
            preserve_lock_file=True,
        ),
        pytest.raises(errors.InstallError, match="lifecycle mutation is already in progress"),
    ):
        application.dispatch(command, **arguments)

    for owner in owners.values():
        owner.assert_not_called()


def test_lifecycle_lock_is_released_after_success_and_failure(
    tmp_path: Path,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    recover = mocker.patch.object(
        application.control,
        "recover",
        side_effect=[
            {"state": "not_required"},
            errors.InstallError("failed"),
            {"state": "closed"},
        ],
    )

    assert application.dispatch("recover", port=ctx.port) == {"state": "not_required"}
    with pytest.raises(errors.InstallError, match="failed"):
        application.dispatch("recover", port=ctx.port)
    assert application.dispatch("recover", port=ctx.port) == {"state": "closed"}
    assert recover.call_count == 3


def test_unavailable_lifecycle_lock_is_a_bounded_public_error(
    tmp_path: Path,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    lock = mocker.patch.object(application, "FileLock").return_value
    lock.acquire.side_effect = OSError("private host detail")
    recover = mocker.patch.object(application.control, "recover")

    code, stdout, stderr = invoke("recover", "--json", "--port", str(ctx.port))

    assert code == 2
    assert stdout == ""
    assert json.loads(stderr) == {
        "error": {
            "code": "lifecycle_error",
            "message": "lifecycle mutation lock is unavailable",
            "next": "openai-responses-proxy doctor",
        }
    }
    assert "private host detail" not in stderr
    assert "Traceback" not in stderr
    recover.assert_not_called()


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_read_only_commands_remain_available_during_lifecycle_mutation(
    tmp_path: Path,
    command: str,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    evidence = {"state": "not_installed", "detail": "absent"}
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    status = mocker.patch.object(application.control, "status", return_value=evidence)

    with FileLock(
        application._lifecycle_lock_path(ctx),
        timeout=0,
        fallback_to_soft=False,
        preserve_lock_file=True,
    ):
        result = application.dispatch(command, port=ctx.port)

    status.assert_called_once_with(ctx)
    assert result is not None
    if command == "status":
        assert result == evidence
    else:
        assert result["state"] == "not_installed"
