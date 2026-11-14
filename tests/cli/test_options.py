"""Contracts for the option grammar that the public commands share."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai_responses_proxy.cli import application
from tests.cli.fixtures import invoke

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class OptionGrammarContracts:
    """Keep every option value inside its grammar before a command runs."""

    def test_ports_and_deadlines_are_rejected_at_the_command_boundary(
        self, *, mocker: MockerFixture
    ) -> None:
        # A value that passed the grammar would run the command on this host.
        execute = mocker.patch.object(application, "_execute", return_value=0)
        cases = (
            ("status", "--port", "0"),
            ("status", "--port", "65536"),
            # A decimal that overflows a float reached the conversion as an unhandled
            # OverflowError until Cyclopts 5.4.2: a traceback and exit status 1.
            ("status", "--port", "1.0e999"),
            ("status", "--port", "-1.5e400"),
            ("status", "--port="),
            # A long digit string met the interpreter's integer-conversion limit.
            ("status", "--port", "9" * 5000),
            ("status", "--port", "100000"),
            # A port is written in ASCII decimal digits. Each of these was accepted, and
            # a fraction and a base prefix ran on another port: 8.5 as 8, 9.5 as 10,
            # 0x50 as 80.
            ("status", "--port", "8.5"),
            ("status", "--port", "9.5"),
            ("status", "--port", "8080.0"),
            ("status", "--port", "0x50"),
            ("status", "--port", "8_080"),
            ("status", "--port", "+8080"),
            ("status", "--port", "\uff18\uff10\uff18\uff10"),
            ("uninstall", "--port", "8.5"),
            ("reload", "--timeout-seconds", "0"),
            ("reload", "--timeout-seconds", "-1"),
            ("reload", "--timeout-seconds", "nan"),
            # A deadline has an end: these were accepted without an upper bound.
            ("reload", "--timeout-seconds", "inf"),
            ("reload", "--timeout-seconds", "1e999"),
            ("reload", "--timeout-seconds", "1e308"),
            ("reload", "--timeout-seconds", "3600.5"),
            ("rollback", "--to-release", "1.0.0", "--timeout-seconds", "inf"),
        )
        for arguments in cases:
            code, stdout, stderr = invoke(*arguments)
            assert code == 2
            assert stdout == ""
            assert f"openai-responses-proxy {arguments[0]} --help" in stderr
            assert "openai-responses-proxy doctor" not in stderr
            assert "Traceback" not in stderr
            assert "Warning" not in stderr
            assert "set_int_max_str_digits" not in stderr
        execute.assert_not_called()

    def test_ports_and_deadlines_inside_the_grammar_reach_the_command(
        self, *, mocker: MockerFixture
    ) -> None:
        execute = mocker.patch.object(application, "_execute", return_value=0)

        assert invoke("status", "--port", "08080")[0] == 0
        assert execute.call_args.kwargs["port"] == 8080
        assert invoke("status", "--port=65535")[0] == 0
        assert execute.call_args.kwargs["port"] == 65535
        assert invoke("status", "--port", "0" * 5000 + "80")[0] == 0
        assert execute.call_args.kwargs["port"] == 80
        assert invoke("reload", "--timeout-seconds", "3600")[0] == 0
        assert execute.call_args.kwargs["timeout_seconds"] == 3600.0
        assert invoke("reload", "--timeout-seconds", "0.5")[0] == 0
        assert execute.call_args.kwargs["timeout_seconds"] == 0.5
