"""Bounded input-capacity recovery preserves current media and dependencies."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.relay import exchange as upstream_exchange
from tests.relay.fixtures import EXACT_ERROR
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import request
from tests.relay.fixtures import request_body
from tests.relay.fixtures import running_proxy

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from openai_responses_proxy.relay.upstream import UpstreamResponse


class TestCapacityRecovery(InputTransportFixture):
    @pytest.mark.parametrize(
        "message",
        [
            "Input is too long",
            "media inputs exceed the remaining encoded media budget after processing",
        ],
    )
    def test_budget_rejection_retries_smaller_history_and_preserves_current_images(
        self, message: str
    ) -> None:
        image = {"type": "input_image", "image_url": "https://example.invalid/current.png"}
        current = [
            {"type": "message", "role": "user", "content": [image]},
            {"type": "function_call", "call_id": "c", "name": "view", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": [image]},
        ]
        body = json.dumps(
            {
                "instructions": "required policy",
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
                    *current,
                ],
            }
        ).encode()
        error = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "validation_error",
                    "param": "",
                    "message": message + " (request id: fixture)",
                }
            }
        ).encode()
        success = b'{"id":"resp_recovered","status":"completed"}'
        with running_proxy([(400, error), (200, success)]) as (port, received):
            try:
                with request(port, body) as result:
                    assert result.read() == success
            except urllib.error.HTTPError as failure:
                failure.close()
                raise
        assert len(received) == 2
        assert len(received[1]) < len(received[0])
        recovered = json.loads(received[1])
        assert recovered["input"] == current
        assert recovered["instructions"] == "required policy"

    def test_media_budget_rejection_preserves_same_turn_images_without_unsafe_retry(self) -> None:
        image = {"type": "input_image", "image_url": "https://example.invalid/page.png"}
        items: list[dict[str, object]] = [
            {"type": "message", "role": "user", "content": "Compare these pages"}
        ]
        for index in range(32):
            items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": str(index),
                        "name": "view",
                        "arguments": "{}",
                    },
                    {"type": "function_call_output", "call_id": str(index), "output": [image]},
                ]
            )
        body = json.dumps({"input": items}).encode()
        error = (
            b'{"error":{"type":"invalid_request_error","code":"validation_error",'
            b'"param":"","message":"media inputs exceed the remaining encoded media budget '
            b'after processing"}}'
        )
        with running_proxy([(400, error)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as result:
                assert result.code == 400
                assert result.read() == error
        assert len(received) == 1
        assert json.loads(received[0])["input"] == items

    @pytest.mark.parametrize(
        "rejected",
        [
            (
                b'{"error":{"type":"invalid_request_error","code":"validation_error",'
                b'"param":"","message":"Input is too long"}}'
            ),
            EXACT_ERROR,
            b'{"error":{"type":"server_error","code":"response_failed"}}',
        ],
    )
    def test_capacity_recovery_relays_second_rejection_without_cascading_recovery(
        self, rejected: bytes
    ) -> None:
        capacity = (
            b'{"error":{"type":"invalid_request_error","code":"validation_error",'
            b'"param":"","message":"Input is too long"}}'
        )
        with running_proxy([(400, capacity), (400, rejected)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, request_body())
            with raised.value as result:
                assert result.code == 400
                assert result.read() == rejected
        assert len(received) == 2
        assert len(received[1]) < len(received[0])
        counters, _classifications = self._status_maps()
        assert counters["input_capacity_recovery_attempts"] == 1
        assert counters["input_capacity_recovery_rejected"] == 1
        assert counters["input_variant_dialogue_recovery_attempts"] == 0
        assert counters["response_failed_dialogue_recovery_attempts"] == 0

    def test_capacity_recovery_keeps_original_call_for_current_delayed_delivery(self) -> None:
        required = [
            {"type": "function_call", "call_id": "job", "name": "run", "arguments": "{}"},
            {
                "type": "function_call_output",
                "id": "initial",
                "call_id": "job",
                "output": "started",
            },
            {"type": "message", "role": "user", "content": "current request"},
            {
                "type": "function_call_output",
                "id": "later",
                "call_id": "job",
                "name": "run",
                "output": "completed",
            },
        ]
        body = json.dumps(
            {"input": [{"type": "message", "role": "user", "content": "old" * 1000}, *required]}
        ).encode()
        capacity = (
            b'{"error":{"type":"invalid_request_error","code":"validation_error",'
            b'"param":"","message":"Input is too long"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        with (
            running_proxy([(400, capacity), (200, success)]) as (port, received),
            request(port, body) as result,
        ):
            assert result.read() == success
        assert len(received) == 2
        assert json.loads(received[1])["input"] == json.loads(received[0])["input"][1:]
        assert json.loads(received[1])["input"][0] == required[0]

    def test_capacity_recovery_transport_failure_is_terminal(
        self, *, mocker: MockerFixture
    ) -> None:
        capacity = (
            b'{"error":{"type":"invalid_request_error","code":"validation_error",'
            b'"param":"","message":"Input is too long"}}'
        )
        with running_proxy([(400, capacity)]) as (port, received):
            real_urlopen = upstream_exchange.urlopen_direct
            calls = 0

            def fail_second(
                outbound: urllib.request.Request, timeout: float, *, deadline: float
            ) -> UpstreamResponse:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise urllib.error.URLError("private-upstream-detail")
                return real_urlopen(outbound, timeout, deadline=deadline)

            mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=fail_second)
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, request_body())
            with raised.value as result:
                assert result.code == 502
                assert (
                    json.loads(result.read())["error"]["code"]
                    == "input_capacity_recovery_transport_error"
                )
        assert calls == 2
        assert len(received) == 1
