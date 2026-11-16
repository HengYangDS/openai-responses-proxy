"""The last line of an admitted response before its release names how the response ended."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

import pytest

from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import responses
from tests.relay.endings.fixtures import CURRENT
from tests.relay.endings.fixtures import ENDINGS
from tests.relay.endings.fixtures import PATH
from tests.relay.endings.fixtures import PROVIDERS
from tests.relay.endings.fixtures import UNREAD
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from tests.relay.endings.fixtures import Ending


def _lines() -> list[str]:
    return Path(operational_log.LOG_PATH).read_text(encoding="utf-8").splitlines()


def _fields(line: str) -> dict[str, str]:
    """Return the fields of one line; a bare word, which redaction can leave, is no field."""
    return dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)


class TestAdmittedResponseEndings(InputTransportFixture):
    """The log of one admitted response holds its ending last before its release."""

    @pytest.mark.parametrize("ending", ENDINGS.values(), ids=ENDINGS)
    def test_the_last_line_before_the_release_names_the_ending(
        self, ending: Ending, *, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        if ending.arrange is not None:
            ending.arrange(mocker)
        attempts = ending.upstream()
        opened = mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=attempts)
        handler = MemoryHandler(ending.body, path=ending.path)
        writer = None
        if ending.write is not None:
            writer = mocker.Mock(**{"write.side_effect": ending.write("private")})
            handler.wfile = writer

        if ending.leaves is None:
            responses.relay(handler, "POST", PROVIDERS)
        else:
            with pytest.raises(ending.leaves):
                responses.relay(handler, "POST", PROVIDERS)

        lines = _lines()
        events = [_fields(line)["event"] for line in lines]
        admitted = events.index("responses_admitted")
        assert events[-1] == "responses_released"
        # The rule: the last line before the release names the ending, and the lines before it
        # name steps.
        assert events[-2] == ending.named
        assert ending.holds in lines[-2]
        assert events[admitted + 1 : -2] == list(ending.steps)
        assert opened.call_count == len(attempts)
        # One response gets one answer, and nothing is written to a connection after a write
        # to it has failed.
        assert len(handler.statuses) == 1
        if writer is not None:
            assert writer.write.call_count == 1
        if ending.closed:
            assert getattr(handler, "close_connection", False)
        if ending.failure != UNREAD:
            failure = self._status_snapshot()["last_failure"]
            assert (failure and cast("dict[str, object]", failure)["classification"]) == (
                ending.failure
            )
        if ending.client is not None:
            status, code = ending.client
            assert handler.statuses == [status]
            if code is not None:
                assert json.loads(handler.output())["error"]["code"] == code
        assert self._status_snapshot()["active_responses"] == 0

    def _json_terminal(self, body: bytes, mocker: MockerFixture) -> dict[str, str]:
        mocker.patch.object(upstream_exchange, "urlopen_direct", return_value=DirectResponse(body))
        handler = MemoryHandler(CURRENT, path=PATH)

        responses.relay(handler, "POST", PROVIDERS)

        assert handler.statuses == [200]
        assert handler.output() == body
        [fields] = [
            fields for line in _lines() if (fields := _fields(line))["event"] == "json_terminal"
        ]
        return fields

    @pytest.mark.parametrize("status", ["completed", "incomplete"])
    def test_a_completed_response_that_is_not_a_stream_names_its_status(
        self, status: str, *, mocker: MockerFixture
    ) -> None:
        body = json.dumps({"id": "resp_done", "status": status, "output": []}).encode()

        fields = self._json_terminal(body, mocker)

        assert fields == {
            "req": "1",
            "event": "json_terminal",
            "provider": "ucloud",
            "path": PATH,
            "status": status,
        }
