"""Provider route isolation and common projection contracts."""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
from pathlib import Path

import pytest

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy

ROOT = Path(__file__).resolve().parents[2]


def _body(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


class ProviderRouteTests:
    def setup_method(self) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()

    def test_new_provider_uses_the_existing_responses_path(self) -> None:
        body = _body(
            {
                "input": [{"type": "message", "role": "user", "content": "hello"}],
                "store": False,
            }
        )
        success = b'{"id":"resp_new","status":"completed"}'

        with (
            running_proxy([(200, success)], extra_provider="new-gateway") as (port, received),
            request(port, body, path="/new-gateway/v1/responses") as response,
        ):
            assert response.status == 200
            assert response.read() == success

        assert received == [body]

    def test_all_three_routes_forward_the_same_portable_body(self, subtests) -> None:
        raw = _body(
            {
                "previous_response_id": "provider-response",
                "include": ["reasoning.encrypted_content"],
                "input": [
                    {
                        "type": "additional_tools",
                        "id": "current-tools",
                        "role": "developer",
                        "tools": [{"type": "namespace", "name": "functions", "tools": []}],
                    },
                    {
                        "type": "reasoning",
                        "id": "rs_old",
                        "encrypted_content": "opaque",
                    },
                    {
                        "type": "message",
                        "id": "msg_old",
                        "status": "completed",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "prior answer",
                                "annotations": [],
                            },
                            {"type": "refusal", "refusal": "prior refusal"},
                        ],
                    },
                    {"type": "message", "role": "user", "content": "continue"},
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": [{"type": "output_text", "text": "tool result"}],
                    },
                    {"type": "compaction_trigger"},
                ],
            }
        )
        success = b'{"id":"resp_ok","status":"completed"}'

        with running_proxy([(200, success)] * 4) as (port, received):
            for route in ("dmxapi", "ucloud", "aihubmix", "dmxapi"):
                with (
                    subtests.test(route=route),
                    request(port, raw, path=f"/{route}/v1/responses") as response,
                ):
                    assert response.status == 200
                    assert response.read() == success

        assert len(received) == 4
        assert len(set(received)) == 1
        for forwarded in received:
            payload = json.loads(forwarded)
            assert "previous_response_id" not in payload
            assert payload["include"] == []
            catalog, assistant, user, call, output, trigger = payload["input"]
            assert catalog == {
                "type": "additional_tools",
                "id": "current-tools",
                "role": "developer",
                "tools": [{"type": "namespace", "name": "functions", "tools": []}],
            }
            assert assistant == {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": "prior answerprior refusal",
            }
            assert user == {"type": "message", "role": "user", "content": "continue"}
            assert call["call_id"] == output["call_id"]
            assert output["output"] == [{"type": "input_text", "text": "tool result"}]
            assert trigger == {"type": "compaction_trigger"}

    def test_unknown_route_and_unknown_replay_shape_never_reach_upstream(self, subtests) -> None:
        with running_proxy([]) as (port, received):
            for path, body, expected_code in (
                ("/unknown/v1/responses", _body({"input": "hello"}), 404),
                (
                    "/ucloud/v1/responses",
                    _body({"input": [{"type": "future_item", "opaque": True}]}),
                    400,
                ),
            ):
                with (
                    subtests.test(path=path),
                    pytest.raises(urllib.error.HTTPError) as raised,
                ):
                    request(port, body, path=path)
                with raised.value as error:
                    assert error.code == expected_code
            assert received == []

    def test_empty_request_and_ambiguous_routes_never_reach_upstream(
        self, subtests, *, mocker
    ) -> None:
        valid = _body({"input": [{"type": "message", "role": "user", "content": "hello"}]})
        cases = (
            ("/ucloud/v1/responses", b"", 400),
            ("/ucloud/v1/models", valid, 404),
            ("/ucloud/v1/responsesx", valid, 404),
            ("/ucloud/v1//responses", valid, 404),
            ("/ucloud/v1/../admin", valid, 404),
            ("/ucloud/v1/%2e%2e/admin", valid, 404),
        )
        log = mocker.patch("openai_responses_proxy.relay.operational_log.log")
        with running_proxy([]) as (port, received):
            for path, body, status in cases:
                with (
                    subtests.test(path=path),
                    pytest.raises(urllib.error.HTTPError) as raised,
                ):
                    request(port, body, path=path)
                with raised.value as error:
                    assert error.code == status
            assert received == []
        assert log.call_count == len(cases)

    def test_rejected_routes_close_before_an_unread_body_can_be_reparsed(self, subtests) -> None:
        body = b'{"ignored":true}'
        with running_proxy([]) as (port, received):
            for path, method in (
                ("/unknown/v1/responses", b"POST"),
                ("/ucloud/v1/models", b"POST"),
            ):
                with subtests.test(path=path):
                    wire = raw_exchange(
                        port,
                        method
                        + b" "
                        + path.encode()
                        + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                        + b"Content-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                        + body
                        + b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                    )
                    assert wire.count(b"HTTP/1.1 ") == 1
                    assert b"HTTP/1.1 404 " in wire
                    assert b"HTTP/1.1 400 " not in wire
            assert received == []

    @pytest.mark.parametrize(
        ("headers", "reason"),
        [
            (b"Content-Length: not-a-length", "invalid_content_length"),
            (b"Content-Length: 999999999999999999999999", "invalid_content_length"),
            (b"Content-Length: -2", "invalid_content_length"),
            (b"Content-Length: +2", "invalid_content_length"),
            (b"Content-Length: 2, 2", "invalid_content_length"),
            (b"Content-Length: 2\r\nContent-Length: 2", "ambiguous_content_length"),
            (b"Transfer-Encoding: chunked\r\nContent-Length: 2", "unsupported_transfer_encoding"),
            (b"Transfer-Encoding: chunked", "unsupported_transfer_encoding"),
            (b"", "missing_content_length"),
        ],
    )
    def test_invalid_request_framing_is_rejected_before_upstream_io(
        self, headers: bytes, reason: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with running_proxy([]) as (port, received):
            wire = raw_exchange(
                port,
                b"POST /ucloud/v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + headers
                + b"\r\nConnection: keep-alive\r\n\r\n{}"
                + b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            )
            assert wire.count(b"HTTP/1.1 ") == 1
            assert b"HTTP/1.1 400 " in wire
            assert b"Connection: close\r\n" in wire
            payload = json.loads(wire.partition(b"\r\n\r\n")[2])
            assert payload["error"]["code"] == "request_framing_rejected"
            assert payload["error"]["reason"] == reason
            assert b"not-a-length" not in wire
            assert received == []
        assert "Traceback" not in capsys.readouterr().err

    def test_valid_request_framing_preserves_one_keep_alive_request(self) -> None:
        success = b'{"id":"resp_framed","status":"completed"}'
        captures: list[dict[str, object]] = []
        with running_proxy([(200, success)], captures=captures) as (port, received):
            wire = raw_exchange(
                port,
                b"POST /ucloud/v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: 17\r\nContent-Type: application/json\r\n\r\n"
                b'{"input":"hello"}'
                b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            )
            assert wire.count(b"HTTP/1.1 200 ") == 2
            assert success in wire
            assert json.loads(received[0]) == {"input": "hello", "store": False}
            headers = captures[0]["headers"]
            assert isinstance(headers, dict)
            assert headers["Content-Length"] == str(len(received[0]))

    def test_truncated_request_does_not_forward_a_valid_json_prefix(self) -> None:
        success = b'{"id":"resp_not_admitted","status":"completed"}'
        with running_proxy([(200, success)]) as (port, received):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                client.sendall(
                    b"POST /ucloud/v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Content-Length: 18\r\nConnection: close\r\n\r\n"
                    b'{"input":"hello"}'
                )
                client.shutdown(socket.SHUT_WR)
                with http.client.HTTPResponse(client) as response:
                    response.begin()
                    assert response.status == 400
                    raw = response.read()
            payload = json.loads(raw)
            assert payload["error"]["code"] == "request_framing_rejected"
            assert payload["error"]["reason"] == "incomplete_request_body"
            assert b"hello" not in raw
            assert received == []

    def test_all_three_catalog_routes_relay_once_without_responses_policy(self, subtests) -> None:
        payload = b'{"object":"list","data":[]}'
        captures: list[dict[str, object]] = []
        with running_proxy([(200, payload)] * 3, captures=captures) as (port, received):
            for route in ("dmxapi", "ucloud", "aihubmix"):
                with (
                    subtests.test(route=route),
                    request(port, path=f"/{route}/v1/models?limit=1", method="GET") as response,
                ):
                    assert response.status == 200
                    assert response.read() == payload
        assert received == [b"", b"", b""]
        assert [capture["method"] for capture in captures] == ["GET", "GET", "GET"]
        assert [capture["path"] for capture in captures] == ["/models?limit=1"] * 3

    def test_catalog_http_error_is_relayed_once_without_responses_recovery(
        self,
    ) -> None:
        payload = b'{"error":{"type":"catalog_denied"}}'
        with running_proxy([(403, payload)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, path="/dmxapi/v1/models", method="GET")
            with raised.value as error:
                assert error.code == 403
                assert error.read() == payload
        assert received == [b""]

    def test_dmx_empty_response_cooldown_does_not_block_ucloud(self) -> None:
        empty = _body(
            {
                "error": {
                    "message": "official provider returned an empty response",
                    "type": "dmx_api_error",
                    "code": "empty_response",
                }
            }
        )
        success = b'{"id":"resp_ucloud","status":"completed"}'
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([(477, empty), (477, empty), (200, success)]) as (
            port,
            received,
        ):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body, path="/dmxapi/v1/responses")
            with raised.value as error:
                assert error.code == 503
            with request(port, body, path="/ucloud/v1/responses") as response:
                assert response.status == 200
                assert response.read() == success

        assert len(received) == 3

    def test_exhausted_503_cooldown_is_provider_scoped_over_loopback(self) -> None:
        unavailable = _body({"error": {"type": "server_error", "code": "overloaded"}})
        success = b'{"id":"resp_other","status":"completed"}'
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([*[(503, unavailable)] * 4, (200, success)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as first:
                request(port, body, path="/ucloud/v1/responses")
            with first.value as error:
                assert error.code == 503
                assert error.read() == unavailable

            with pytest.raises(urllib.error.HTTPError) as second:
                request(port, body, path="/ucloud/v1/responses")
            with second.value as error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "5"
                assert json.loads(error.read())["error"]["code"] == "provider_unavailable_cooldown"

            with request(port, body, path="/aihubmix/v1/responses") as response:
                assert response.status == 200
                assert response.read() == success

        assert len(received) == 5

    def test_non_dmx_http_477_is_not_given_dmx_recovery(self) -> None:
        empty = _body(
            {
                "error": {
                    "message": "official provider returned an empty response",
                    "type": "dmx_api_error",
                    "code": "empty_response",
                }
            }
        )
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([(477, empty)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body, path="/aihubmix/v1/responses")
            with raised.value as error:
                assert error.code == 477
                assert error.read() == empty

        assert len(received) == 1
