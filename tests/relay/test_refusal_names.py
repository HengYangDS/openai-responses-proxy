"""Each data-plane refusal answers with its error code and is counted and logged under it."""

from __future__ import annotations

import json
import urllib.error
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import json_body
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy
from tests.relay.fixtures import upstream_path

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


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
