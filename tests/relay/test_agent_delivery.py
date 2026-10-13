"""The real HTTP relay selects and restores one delegation contract."""

from __future__ import annotations

import http.client
import json
import urllib.error

import pytest

from openai_responses_proxy.protocol.agent_delivery import PLAINTEXT_NAMESPACE
from tests.protocol.test_agent_delivery import _call
from tests.protocol.test_agent_delivery import _tools
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy


class AgentDeliveryRelayContracts:
    @pytest.mark.parametrize("stream", [False, True])
    def test_terminal_empty_agent_call_never_commits_as_success(self, stream: bool) -> None:
        call = {**_call(), "status": "in_progress", "arguments": ""}
        terminal = {"status": "completed", "output": [call]}
        if stream:
            payload = b"".join(
                b"data: " + json.dumps(event).encode() + b"\n\n"
                for event in (
                    {"type": "response.output_item.added", "item": call},
                    {"type": "response.output_item.done", "item": call},
                    {"type": "response.completed", "response": terminal},
                )
            )
            response = {"headers": {"Content-Type": "text/event-stream"}, "payload": payload}
        else:
            response = (200, json.dumps(terminal).encode())
        with running_proxy([response], agent_message_delivery="plaintext") as (port, received):
            body = json.dumps({"stream": stream, "tools": _tools(), "input": "run test"}).encode()
            if stream:
                with request(port, body, path="/ucloud/v1/responses") as result:
                    with pytest.raises(http.client.IncompleteRead) as raised:
                        result.read()
                    events = [
                        json.loads(line[6:])
                        for line in raised.value.partial.splitlines()
                        if line.startswith(b"data: ")
                    ]
                    assert [event["type"] for event in events] == ["response.output_item.added"]
                    assert events[0]["item"]["namespace"] == "collaboration"
                    assert events[0]["item"]["arguments"] == ""
            else:
                with pytest.raises(urllib.error.HTTPError) as raised:
                    request(port, body, path="/ucloud/v1/responses")
                assert raised.value.code == 503
                assert json.loads(raised.value.read())["error"]["code"] == (
                    "invalid_responses_success_body"
                )
                raised.value.close()
            assert len(received) == 1

    @pytest.mark.parametrize("stream", [False, True])
    def test_real_http_roundtrip_retains_arguments_and_call_identity(self, stream: bool) -> None:
        call = _call()
        terminal = {"status": "completed", "output": [call]}
        if stream:
            payload = b"".join(
                b"data: " + json.dumps(event).encode() + b"\n\n"
                for event in (
                    {
                        "type": "response.output_item.added",
                        "item": {**call, "status": "in_progress", "arguments": ""},
                    },
                    {"type": "response.output_item.done", "item": call},
                    {"type": "response.completed", "response": terminal},
                )
            )
            response = {"headers": {"Content-Type": "text/event-stream"}, "payload": payload}
        else:
            response = (200, json.dumps(terminal).encode())
        with running_proxy([response], agent_message_delivery="plaintext") as (port, received):
            raw_request = json.dumps(
                {"stream": stream, "tools": _tools(), "input": "run test"}
            ).encode()
            with request(port, raw_request, path="/ucloud/v1/responses") as result:
                raw = result.read()
                assert result.status == 200
            sent = json.loads(received[0])
            assert sent["tools"][0]["name"] == PLAINTEXT_NAMESPACE
            assert (
                "encrypted"
                not in sent["tools"][0]["tools"][0]["parameters"]["properties"]["message"]
            )
            if stream:
                events = [
                    json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ")
                ]
                output = events[-1]["response"]["output"][0]
                assert events[0]["item"]["arguments"] == ""
            else:
                output = json.loads(raw)["output"][0]
            assert output["namespace"] == "collaboration"
            assert output["encrypted_function_args"] == []
            assert output["arguments"] == call["arguments"]
            assert output["call_id"] == call["call_id"]

    def test_alias_collision_declines_locally_without_upstream_request(self) -> None:
        tools = [*_tools(), {"type": "namespace", "name": PLAINTEXT_NAMESPACE, "tools": []}]
        with running_proxy([], agent_message_delivery="plaintext") as (port, received):
            body = json.dumps({"tools": tools, "input": "hello"}).encode()
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body, path="/ucloud/v1/responses")
            assert raised.value.code == 400
            assert json.loads(raised.value.read())["error"]["code"] == "agent_delivery_rejected"
            raised.value.close()
            assert not received
