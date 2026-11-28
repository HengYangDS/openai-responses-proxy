"""A relayed response passes on the upstream's Server and Date; a local one carries the proxy's."""

from __future__ import annotations

import urllib.error
from typing import TYPE_CHECKING
from typing import cast

import pytest

from openai_responses_proxy import product_identity
from openai_responses_proxy.relay import sse
from tests.relay.fixtures import UPSTREAM_DATE
from tests.relay.fixtures import UPSTREAM_SERVER
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import json_body
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy
from tests.relay.fixtures import upstream_path

if TYPE_CHECKING:
    from email.message import Message

    from pytest_mock import MockerFixture


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


class RelayedFramingTests:
    """A relayed response carries only end-to-end fields and the framing that its status allows."""

    def test_a_reconnected_stream_carries_the_fields_of_the_stream_it_delivers(
        self, *, mocker: MockerFixture
    ) -> None:
        failed = (
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
        )
        reopened = DirectResponse(b'data: {"type":"response.completed"}\n\n')
        send_headers = mocker.Mock()
        mocker.patch.object(sse.time, "sleep", return_value=None)
        sse.relay(
            MemoryHandler(),
            DirectResponse(failed),
            "/v1/responses",
            1,
            reopen=mocker.Mock(return_value=reopened),
            send_headers=send_headers,
        )
        send_headers.assert_called_once_with(reopened)

    def test_fields_that_connection_names_stay_on_their_hop(self) -> None:
        catalog = {
            "status": 200,
            "content_type": "application/json",
            "chunks": [b'{"object":"list","data":[]}'],
            "headers": {"Connection": "x-hop", "X-Hop": "1", "Trailer": "X-Sum", "X-Kept": "1"},
        }
        captures: list[dict[str, object]] = []
        with running_proxy([catalog], captures=captures) as (port, _):
            wire = raw_exchange(
                port,
                b"GET "
                + upstream_path("models").encode()
                + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close, x-private\r\n"
                + b"X-Private: secret\r\nX-Shared: 1\r\n\r\n",
            )
        head = wire.partition(b"\r\n\r\n")[0].lower()
        assert b"\r\nx-kept: 1" in head
        assert b"x-hop" not in head
        assert b"trailer" not in head
        (capture,) = captures
        sent = {str(name).lower() for name in cast("dict[str, str]", capture["headers"])}
        assert "x-shared" in sent
        assert "x-private" not in sent

    @pytest.mark.parametrize("status", [204, 304])
    def test_a_bodiless_status_is_relayed_without_a_body(self, status: int) -> None:
        with running_proxy([{"status": status}]) as (port, _):
            wire = raw_exchange(
                port,
                b"GET "
                + upstream_path("models").encode()
                + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            )
        head, _, body = wire.partition(b"\r\n\r\n")
        assert head.startswith(f"HTTP/1.1 {status} ".encode())
        assert b"transfer-encoding" not in head.lower()
        assert body == b""
