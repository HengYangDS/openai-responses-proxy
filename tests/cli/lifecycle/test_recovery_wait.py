"""``recover`` tells a person that it waits, and keeps its result and its JSON form as they are.

Before it restores a transaction that reached activation, ``recover`` waits two minutes for a
handoff request that may still be pending. The human form says so in one line on standard
error before the wait; ``--json`` writes nothing there. The clock is patched: no test waits.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.cli import application
from openai_responses_proxy.cli import presentation
from tests.cli.fixtures import invoke
from tests.lifecycle.deployment.handoff.fixtures import PolledClock
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.transaction.fixtures import interrupted_transition

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

_WAITING = "Recovery waits 121 seconds for a handoff request that may still be pending.\n"


@pytest.mark.parametrize(
    ("reached", "progress"),
    [("activated", _WAITING), ("materialized", "")],
    ids=["a transaction that waits", "a transaction that does not"],
)
@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
def test_recover_writes_its_wait_to_standard_error_only_in_the_human_form(
    tmp_path: Path, *, reached: str, progress: str, json_output: bool, mocker: MockerFixture
) -> None:
    ctx = install_context(tmp_path)
    interrupted = interrupted_transition(ctx, mocker=mocker, reached=reached, preserved=True)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "read_runtime", return_value=interrupted.at_rest)
    native = mocker.patch.object(application.control, "adapter").return_value
    native.configured_executable.side_effect = lambda selected: selected.executable
    clock = PolledClock(mocker)
    result = {
        "transaction_id": interrupted.transaction.expected["transaction_id"],
        "version": "1.2.3",
        "state": "rolled_back",
    }

    code, stdout, stderr = invoke("recover", *(("--json",) if json_output else ()))

    assert code == 0
    assert clock.sleep.called is (reached == "activated")
    if json_output:
        assert json.loads(stdout) == {**result, "next": "openai-responses-proxy status"}
        assert stderr == ""
    else:
        assert stdout == presentation.render("recover", result) + "\n"
        assert stderr == progress
