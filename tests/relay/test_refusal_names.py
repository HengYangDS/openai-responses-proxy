"""Each data-plane refusal answers with its error code and is counted and logged under it."""

from __future__ import annotations

import json
import urllib.error
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import json_body
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy
from tests.relay.fixtures import upstream_path

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def refusal_clock(mocker: MockerFixture) -> list[float]:
    """Give each test its own refusal log interval on a clock that it moves."""
    clock = [100.0]
    mocker.patch.object(
        responses,
        "_LOCAL_CLIENT_REFUSAL_LOG",
        operational_log.Throttle(
            responses.LOCAL_CLIENT_REFUSAL_LOG_SECONDS, clock=lambda: clock[0]
        ),
    )
    return clock


def _counters() -> dict[str, object]:
    snapshot = telemetry.status(
        release="test",
        serving_payload_sha256=None,
        release_receipt_sha256=None,
        admission={},
        runtime_identity={},
    )
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    return {str(name): value for name, value in counters.items()}


def _events(log: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in log.call_args_list]


class RefusalNameTests:
    def test_a_refused_web_page_gets_the_local_error_envelope(
        self, *, mocker: MockerFixture
    ) -> None:
        telemetry.reset_for_test()
        log = mocker.patch("openai_responses_proxy.relay.operational_log.log")
        with running_proxy([(200, b"{}")]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(
                    port,
                    json_body({"input": "hello"}),
                    path=upstream_path(),
                    headers={"Origin": "https://evil.example"},
                )
            with raised.value as error:
                assert error.code == 403
                assert error.headers["Content-Type"] == "application/json"
                assert error.headers["Connection"] == "close"
                assert json.loads(error.read())["error"] == {
                    "message": "data requests are available only to a local native client",
                    "type": "invalid_request_error",
                    "code": "local_client_required",
                }
            assert received == []
        (event,) = _events(log)
        assert " event=local_client_required " in f" {event} "
        assert _counters()["local_client_required"] == 1

    def test_each_route_refusal_is_named_by_its_error_code(self, *, mocker: MockerFixture) -> None:
        telemetry.reset_for_test()
        log = mocker.patch("openai_responses_proxy.relay.operational_log.log")
        cases = (
            (upstream_path("responsesx"), "POST", "upstream_route_invalid"),
            (upstream_path("models"), "POST", "upstream_route_method_not_allowed"),
        )
        with running_proxy([]) as (port, received):
            for path, method, code in cases:
                with pytest.raises(urllib.error.HTTPError) as raised:
                    request(port, json_body({"input": "hello"}), path=path, method=method)
                with raised.value as error:
                    assert json.loads(error.read())["error"]["code"] == code
            assert received == []
        events = _events(log)
        counters = _counters()
        for _, _, code in cases:
            assert sum(f"event={code} " in event for event in events) == 1
            assert counters[code] == 1
        assert not [name for name in counters if name.startswith("provider_route")]

    def test_repeated_refusals_log_once_per_interval_and_keep_the_last_failure(
        self, *, mocker: MockerFixture, refusal_clock: list[float]
    ) -> None:
        """A web page can repeat this refusal, so it neither floods the log nor hides a fault."""
        telemetry.reset_for_test()
        log = mocker.patch("openai_responses_proxy.relay.operational_log.log")
        page = {"Origin": "https://evil.example"}
        interval = responses.LOCAL_CLIENT_REFUSAL_LOG_SECONDS
        with running_proxy([]) as (port, received):
            for moment in (100.0, 110.0, 120.0, 100.0 + interval):
                refusal_clock[0] = moment
                with pytest.raises(urllib.error.HTTPError) as raised:
                    request(port, json_body({"input": "hello"}), path=upstream_path(), headers=page)
                raised.value.close()
            assert received == []
        events = _events(log)
        assert [event.split(" refused=")[1].split(" ")[0] for event in events] == ["1", "3"]
        assert all(" event=local_client_required " in f" {event} " for event in events)
        assert _counters()["local_client_required"] == 4
        status = telemetry.status(
            release="test",
            serving_payload_sha256=None,
            release_receipt_sha256=None,
            admission={},
            runtime_identity={},
        )
        assert status["last_failure"] is None

    def test_a_refused_web_page_cannot_smuggle_a_second_request(self) -> None:
        body = b'{"ignored":true}'
        with running_proxy([]) as (port, received):
            wire = raw_exchange(
                port,
                b"POST "
                + upstream_path().encode()
                + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: https://evil.example\r\n"
                + b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
                + b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            )
            assert received == []
        assert wire.count(b"HTTP/1.1 ") == 1
        assert wire.startswith(b"HTTP/1.1 403 ")
