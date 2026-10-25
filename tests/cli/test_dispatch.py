"""Public parsing, version identity, and private-role dispatch contracts."""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

from openai_responses_proxy.cli import application
from tests.cli.fixtures import invoke
from tests.lifecycle.fixtures import install_context


def test_source_version_requires_the_real_src_checkout_shape(tmp_path, *, mocker) -> None:
    mocker.patch.object(application, "__file__", str(tmp_path / "installed.py"))
    assert application._source_version() is None


def test_frozen_and_source_release_versions_use_their_bound_owners(tmp_path, *, mocker) -> None:
    (tmp_path / "VERSION").write_text("9.8.7\n", encoding="utf-8")
    mocker.patch.object(sys, "_MEIPASS", str(tmp_path), create=True)
    assert application._release_version() == "9.8.7"
    mocker.patch.object(sys, "_MEIPASS", None)
    mocker.patch.object(application, "_source_version", return_value="2.0.8")
    assert application._release_version() == "2.0.8"


def test_internal_dispatch_argument_contracts_fail_closed(tmp_path, *, mocker) -> None:
    # A private installation context: the operator's live installation, and a lifecycle writer
    # holding its lock, must not decide this argument contract.
    mocker.patch.object(
        application.runtime_context, "create", return_value=install_context(tmp_path)
    )
    cases = (
        (
            "install",
            {
                "asset": "release.tar.gz",
                "trust_anchor": Path("trust"),
                "port": 8792,
            },
        ),
        ("status", {"port": True}),
        ("reload", {"port": 8792, "timeout_seconds": True}),
        ("uninstall", {"port": 8792, "purge": "yes"}),
    )

    for command, arguments in cases:
        with pytest.raises(TypeError):
            application.dispatch(command, **arguments)


def test_subcommand_help_and_parse_errors_are_bounded() -> None:
    for arguments in (("status", "--help"), ("install", "--help")):
        code, stdout, stderr = invoke(*arguments)
        assert code == 0
        assert "Usage:" in stdout
        assert stderr == ""

    code, stdout, stderr = invoke("install")
    assert code == 2
    assert stdout == ""
    assert "required" in stderr

    code, stdout, stderr = invoke("status", "--unknown")
    assert code == 2
    assert stdout == ""
    assert "Unknown option" in stderr


def test_no_command_uses_sys_argv_and_renders_public_help(*, mocker) -> None:
    mocker.patch.object(application.sys, "argv", ["openai-responses-proxy"])
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = application.main()

    assert code == 0
    assert "COMMAND" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_internal_roles_are_exact_and_never_public_commands(*, mocker) -> None:
    activate = mocker.patch.object(application.runtime_spec, "activate")
    executable = mocker.patch.object(
        application.serving_roles,
        "current_executable",
        return_value="/opt/proxy/bin/openai-responses-proxy",
    )
    entrypoint = mocker.patch("openai_responses_proxy.serving.entrypoint.run", return_value=7)
    assert application.main([application.serving_roles.LISTENER_MODE]) == 7
    entrypoint.assert_called_once_with()
    mocker.stop(entrypoint)

    handoff = mocker.patch("openai_responses_proxy.serving.entrypoint.run", return_value=8)
    assert application.main([application.serving_roles.HANDOFF_CHILD_MODE]) == 8
    handoff.assert_called_once_with(handoff_child=True)
    mocker.stop(handoff)

    watchdog = mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.watchdog.run",
        return_value=None,
    )
    assert application.main([application.serving_roles.WATCHDOG_MODE]) == 0
    watchdog.assert_called_once_with()
    mocker.stop(watchdog)

    assert application.main([application.serving_roles.PREWARM_MODE]) == 0
    code, stdout, stderr = invoke("--help")
    assert code == 0
    assert application.serving_roles.PREWARM_MODE not in stdout
    assert stderr == ""

    assert activate.call_count == 3
    assert activate.call_args_list == [
        mocker.call("/opt/proxy/bin/openai-responses-proxy"),
        mocker.call("/opt/proxy/bin/openai-responses-proxy"),
        mocker.call("/opt/proxy/bin/openai-responses-proxy"),
    ]
    assert executable.call_count == 3

    for arguments in (
        [application.serving_roles.LISTENER_MODE, "extra"],
        ["--internal-unknown"],
    ):
        code, stdout, stderr = invoke(*arguments)
        assert code == 2
        assert stdout == ""
        assert "internal" in stderr
