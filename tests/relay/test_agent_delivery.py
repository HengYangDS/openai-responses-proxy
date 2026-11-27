"""Codex's native agent delivery reaches every upstream as Codex sent it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import cast

from openai_responses_proxy.protocol.replay.projection import sanitize_responses_body
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import MemoryHandler

if TYPE_CHECKING:
    import pytest
    from pytest_mock import MockerFixture


def _collaboration_catalog() -> list[dict[str, object]]:
    """Return Codex's collaboration namespace, whose message arguments are encrypted."""
    return [
        {
            "type": "namespace",
            "name": "collaboration",
            "tools": [
                {
                    "type": "function",
                    "name": name,
                    "parameters": {
                        "type": "object",
                        "properties": {"message": {"type": "string", "encrypted": True}},
                    },
                }
                for name in ("spawn_agent", "send_message", "followup_task", "wait_agent")
            ],
        }
    ]


class AgentDeliveryRelayContracts:
    def test_collaboration_catalog_is_forwarded_natively_on_every_released_route(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        body = json.dumps({"tools": _collaboration_catalog(), "input": "delegate"}).encode()
        expected = json.loads(cast("bytes", sanitize_responses_body(body).body))
        for route in provider_registry.load().profiles:
            with subtests.test(route=route):
                admission.reset_for_test()
                telemetry.reset_for_test()
                handler = MemoryHandler(body, path=f"/{route}/v1/responses")
                opened = mocker.patch.object(
                    upstream_exchange,
                    "urlopen_direct",
                    return_value=DirectResponse(b'{"id":"resp_ok","status":"completed"}'),
                )
                responses.relay(handler, "POST", provider_registry.load())
                assert handler.statuses == [200]
                sent = json.loads(opened.call_args.args[0].data)
                assert sent == expected
                assert sent["tools"][0]["name"] == "collaboration"
