"""A route that declares pass-through forwards stateless requests unchanged."""

from __future__ import annotations

import json
import urllib.error

import pytest

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy

SUCCESS = b'{"id":"resp_ok","status":"completed"}'
REFUSED_STATE = (
    b'{"error":{"message":"The encrypted content could not be verified.",'
    b'"type":"invalid_request_error","param":null,"code":"invalid_encrypted_content"}}'
)
CAPACITY = (
    b'{"error":{"type":"invalid_request_error","code":"validation_error",'
    b'"param":"","message":"Input is too long"}}'
)


def _codex_body(*, without: str = "", **fields: object) -> bytes:
    """A stateless Codex request, encoded with spacing that a re-encoding would not keep."""
    payload: dict[str, object] = {
        "model": "gpt-test",
        "store": False,
        "stream": False,
        "prompt_cache_key": "conversation-key",
        "include": ["reasoning.encrypted_content"],
        "input": [
            {"type": "message", "id": "msg_1", "role": "user", "content": "old " * 400},
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"},
            {
                "type": "message",
                "id": "msg_2",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer", "annotations": []}],
            },
            {"type": "message", "id": "msg_3", "role": "user", "content": "continue"},
        ],
    }
    payload.update(fields)
    payload.pop(without, None)
    return json.dumps(payload, indent=1).encode()


def _projected(body: bytes) -> list[object]:
    """Assert that a forwarded body is the portable projection and return its input."""
    payload = json.loads(body)
    assert isinstance(payload, dict)
    assert payload["store"] is False
    assert not {"prompt_cache_key", "previous_response_id", "conversation"} & set(payload)
    assert payload["include"] == []
    items = payload["input"]
    assert isinstance(items, list)
    assert all(item.get("type") != "reasoning" for item in items)
    assert all("id" not in item for item in items)
    return list(items)


class PassThroughRouteTests:
    def setup_method(self) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()

    def test_a_stateless_request_reaches_the_upstream_byte_for_byte(self) -> None:
        body = _codex_body()
        with running_proxy([(200, SUCCESS)] * 2, request_shape="pass-through") as (
            port,
            received,
        ):
            for route in ("dmxapi", "ucloud"):
                with request(port, body, path=f"/{route}/v1/responses") as response:
                    assert response.read() == SUCCESS
        assert received[0] == body
        _projected(received[1])

    @pytest.mark.parametrize(
        "body",
        [
            _codex_body(without="store"),
            _codex_body(store=True),
            _codex_body(previous_response_id="resp_old"),
            _codex_body(conversation="conv_old"),
        ],
        ids=["store-absent", "store-true", "previous-response", "conversation"],
    )
    def test_a_request_that_keeps_state_is_projected(self, body: bytes) -> None:
        with (
            running_proxy([(200, SUCCESS)], request_shape="pass-through") as (port, received),
            request(port, body) as response,
        ):
            assert response.read() == SUCCESS
        _projected(received[0])

    def test_an_unproved_request_is_refused_before_upstream_io(self) -> None:
        body = _codex_body(input=[{"type": "future_item", "opaque": True}])
        with running_proxy([], request_shape="pass-through") as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 400
        assert received == []

    def test_a_refusal_of_state_is_followed_by_one_projected_resend(self) -> None:
        body = _codex_body()
        with (
            running_proxy([(400, REFUSED_STATE), (200, SUCCESS)], request_shape="pass-through") as (
                port,
                received,
            ),
            request(port, body) as response,
        ):
            assert response.read() == SUCCESS
        assert received[0] == body
        _projected(received[1])
        assert len(received) == 2

    def test_a_second_refusal_of_state_reaches_the_client(self) -> None:
        with running_proxy(
            [(400, REFUSED_STATE), (400, REFUSED_STATE)], request_shape="pass-through"
        ) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, _codex_body())
            with raised.value as error:
                assert error.code == 400
                assert error.read() == REFUSED_STATE
        assert len(received) == 2

    def test_a_portable_route_relays_a_refusal_of_state_without_a_resend(self) -> None:
        with running_proxy([(400, REFUSED_STATE)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, _codex_body())
            with raised.value as error:
                assert error.code == 400
        assert len(received) == 1

    def test_capacity_recovery_derives_from_the_projection(self) -> None:
        with (
            running_proxy([(400, CAPACITY), (200, SUCCESS)], request_shape="pass-through") as (
                port,
                received,
            ),
            request(port, _codex_body()) as response,
        ):
            assert response.read() == SUCCESS
        assert len(received) == 2
        recovered = _projected(received[1])
        assert len(received[1]) < len(received[0])
        assert recovered[-1] == {"type": "message", "role": "user", "content": "continue"}
