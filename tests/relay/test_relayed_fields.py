"""A relayed response passes on the upstream's Server and Date; a local one carries the proxy's."""

from __future__ import annotations

import urllib.error
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import product_identity
from tests.relay.fixtures import UPSTREAM_DATE
from tests.relay.fixtures import UPSTREAM_SERVER
from tests.relay.fixtures import json_body
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy
from tests.relay.fixtures import upstream_path

if TYPE_CHECKING:
    from email.message import Message


class RelayedHeaderTests:
    """A relayed response carries the upstream's Server and Date once each."""

    @staticmethod
    def _single_upstream_fields(headers: Message) -> None:
        assert headers.get_all("Server") == [UPSTREAM_SERVER]
        assert headers.get_all("Date") == [UPSTREAM_DATE]

    def test_a_validated_json_response_carries_the_proxy_fields_once(self) -> None:
        payload = json_body({"id": "r", "object": "response", "status": "completed", "output": []})
        with (
            running_proxy([(200, payload)]) as (port, _),
            request(port, json_body({"input": "hello"})) as response,
        ):
            response.read()
            servers = response.headers.get_all("Server") or []
            dates = response.headers.get_all("Date") or []
        assert len(servers) == 1, servers
        assert servers[0].startswith(f"{product_identity.PRODUCT_SLUG}/"), servers
        assert len(dates) == 1, dates
        assert dates != [UPSTREAM_DATE]

    def test_a_relayed_error_carries_the_upstream_fields_once(self) -> None:
        payload = json_body({"error": {"type": "invalid_request_error", "code": "bad"}})
        with running_proxy([(400, payload)]) as (port, _):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, json_body({"input": "hello"}))
            with raised.value as error:
                assert error.code == 400
                self._single_upstream_fields(error.headers)

    def test_a_relayed_catalog_and_stream_carry_the_upstream_fields_once(self) -> None:
        completed = (
            b'data: {"type":"response.completed","response":{"id":"r","status":"completed"}}\n\n'
        )
        stream = {"status": 200, "chunks": [completed]}
        catalog = b'{"object":"list","data":[]}'
        with running_proxy([(200, catalog), stream]) as (port, _):
            with request(port, path=upstream_path("models"), method="GET") as response:
                assert response.read() == catalog
                self._single_upstream_fields(response.headers)
            with request(port, json_body({"input": "hello", "stream": True})) as response:
                response.read()
                self._single_upstream_fields(response.headers)
