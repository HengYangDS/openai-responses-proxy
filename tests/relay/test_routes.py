"""Upstream addressing, request admission and common projection contracts."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.error
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.runtime import config
from tests.relay.fixtures import raw_exchange
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy
from tests.relay.fixtures import upstream_origin
from tests.relay.fixtures import upstream_path

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

ROOT = Path(__file__).resolve().parents[2]


def _body(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _post_line() -> bytes:
    return b"POST " + upstream_path().encode() + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n"


class UpstreamRouteTests:
    def setup_method(self) -> None:
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()

    def test_a_new_upstream_needs_no_release(self) -> None:
        body = _body(
            {
                "input": [{"type": "message", "role": "user", "content": "hello"}],
                "store": False,
            }
        )
        success = b'{"id":"resp_new","status":"completed"}'

        with (
            running_proxy([(200, success)]) as (port, received),
            request(port, body, path=upstream_path(base="/gateway/v1")) as response,
        ):
            assert response.status == 200
            assert response.read() == success

        assert received == [body]

    def test_every_upstream_receives_the_same_portable_body(
        self, subtests: pytest.Subtests
    ) -> None:
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
            for base in ("/a/v1", "/b/v1", "/c/v1", "/a/v1"):
                with (
                    subtests.test(base=base),
                    request(port, raw, path=upstream_path(base=base)) as response,
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

    def test_every_upstream_receives_a_request_that_keeps_no_state(
        self, subtests: pytest.Subtests
    ) -> None:
        # Codex's own stateless form: the client's bytes are never forwarded as they are.
        raw = _body(
            {
                "store": False,
                "prompt_cache_key": "thread-1",
                "include": ["reasoning.encrypted_content"],
                "input": [{"type": "message", "id": "msg_1", "role": "user", "content": "hello"}],
            }
        )
        success = b'{"id":"resp_ok","status":"completed"}'

        with running_proxy([(200, success)] * 3) as (port, received):
            for base in ("/a/v1", "/b/v1", "/c/v1"):
                with (
                    subtests.test(base=base),
                    request(port, raw, path=upstream_path(base=base)) as response,
                ):
                    assert response.status == 200

        assert len(set(received)) == 1
        payload = json.loads(received[0])
        assert "prompt_cache_key" not in payload
        assert payload["include"] == []
        assert payload["input"] == [{"type": "message", "role": "user", "content": "hello"}]

    def test_unaddressed_path_and_unknown_replay_shape_never_reach_upstream(
        self, subtests: pytest.Subtests
    ) -> None:
        with running_proxy([]) as (port, received):
            for path, body, expected_code in (
                ("/unknown/v1/responses", _body({"input": "hello"}), 404),
                (
                    upstream_path(),
                    _body({"input": [{"type": "future_item", "opaque": True}]}),
                    400,
                ),
            ):
                with subtests.test(path=path):
                    with pytest.raises(urllib.error.HTTPError) as raised:
                        request(port, body, path=path)
                    with raised.value as error:
                        assert error.code == expected_code
            assert received == []

    def test_empty_request_and_ambiguous_routes_never_reach_upstream(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        valid = _body({"input": [{"type": "message", "role": "user", "content": "hello"}]})
        log = mocker.patch("openai_responses_proxy.relay.operational_log.log")
        with running_proxy([]) as (port, received):
            cases = (
                (upstream_path(), b"", 400),
                (upstream_path("models"), valid, 404),
                (upstream_path("responsesx"), valid, 404),
                (upstream_path(base="/v1/"), valid, 404),
                (upstream_path(base="/v1/.."), valid, 404),
                (upstream_path(base="/v1/%2e%2e"), valid, 404),
            )
            for path, body, status in cases:
                with subtests.test(path=path):
                    with pytest.raises(urllib.error.HTTPError) as raised:
                        request(port, body, path=path)
                    with raised.value as error:
                        assert error.code == status
            assert received == []
        assert log.call_count == len(cases)

    def test_rejected_routes_close_before_an_unread_body_can_be_reparsed(
        self, subtests: pytest.Subtests
    ) -> None:
        body = b'{"ignored":true}'
        with running_proxy([]) as (port, received):
            for path, method in (
                ("/unknown/v1/responses", b"POST"),
                (upstream_path("models"), b"POST"),
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
                _post_line()
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
                _post_line() + b"Content-Length: 17\r\nContent-Type: application/json\r\n\r\n"
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
                    _post_line() + b"Content-Length: 18\r\nConnection: close\r\n\r\n"
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

    @pytest.mark.parametrize("length", ["4097", "999999999999999999999999"])
    def test_oversized_request_is_rejected_before_reading_its_body(
        self, length: str, *, mocker: MockerFixture
    ) -> None:
        settings = config.load({"OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES": "4096"})
        mocker.patch.object(responses, "_SETTINGS", settings)
        with running_proxy([]) as (port, received):
            wire = raw_exchange(
                port,
                _post_line()
                + f"Content-Length: {length}\r\n".encode()
                + b"Connection: keep-alive\r\n\r\nprivate-unread-content"
                + b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            )
            assert wire.count(b"HTTP/1.1 ") == 1
            assert b"HTTP/1.1 413 " in wire
            assert b"Connection: close\r\n" in wire
            error = json.loads(wire.partition(b"\r\n\r\n")[2])["error"]
            assert error["code"] == "request_body_rejected"
            assert error["reason"] == "request_body_too_large"
            assert b"private-unread-content" not in wire
            assert received == []

    @pytest.mark.parametrize("trickle", [False, True])
    def test_body_progress_cannot_extend_the_total_admission_deadline(
        self, *, trickle: bool, capsys: pytest.CaptureFixture[str], mocker: MockerFixture
    ) -> None:
        settings = config.load({"OPENAI_RESPONSES_PROXY_REQUEST_BODY_TIMEOUT": "0.25"})
        mocker.patch.object(responses, "_SETTINGS", settings)
        stopped = threading.Event()
        with running_proxy([]) as (port, received):
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                client.sendall(
                    _post_line()
                    + b"Content-Length: 4096\r\nConnection: close\r\n\r\nprivate-prefix"
                )

                def progress() -> None:
                    while trickle and not stopped.wait(0.05):
                        try:
                            client.sendall(b" ")
                        except OSError:
                            return

                worker = threading.Thread(target=progress)
                worker.start()
                try:
                    with http.client.HTTPResponse(client) as response:
                        response.begin()
                        assert response.status == 408
                        assert response.headers["Connection"] == "close"
                        raw = response.read()
                        error = json.loads(raw)["error"]
                        assert error["code"] == "request_body_rejected"
                        assert error["reason"] == "request_body_timeout"
                        assert b"private-prefix" not in raw
                finally:
                    stopped.set()
                    worker.join(timeout=1)
                    assert not worker.is_alive()
            assert received == []
        assert "Traceback" not in capsys.readouterr().err

    def test_body_at_the_limit_preserves_bytes_and_keep_alive(
        self, *, mocker: MockerFixture
    ) -> None:
        settings = config.load({"OPENAI_RESPONSES_PROXY_REQUEST_BODY_MAX_BYTES": "4096"})
        mocker.patch.object(responses, "_SETTINGS", settings)
        raw = b'{"store":false,"input":"hello"}'
        body = raw + b" " * (4096 - len(raw))
        success = b'{"id":"resp_bounded","status":"completed"}'
        with running_proxy([(200, success)]) as (port, received):
            wire = raw_exchange(
                port,
                _post_line()
                + b"Content-Length: 4096\r\n\r\n"
                + body
                + b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            )
            assert wire.count(b"HTTP/1.1 200 ") == 2
            assert received == [body]

    @pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e9999"])
    def test_nonfinite_json_is_rejected_without_upstream_io(self, number: bytes) -> None:
        body = b'{"input":"private-current-prompt","temperature":' + number + b"}"
        success = b'{"id":"resp_not_admitted","status":"completed"}'
        with running_proxy([(200, success)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as response:
                assert response.code == 400
                assert response.headers.get("Retry-After") is None
                raw = response.read()
            error = json.loads(raw)["error"]
            assert error["code"] == "provider_portable_projection_rejected"
            assert error["reason"] == "invalid_json"
            assert b"private-current-prompt" not in raw
            assert received == []

    def test_nonstring_replay_role_is_rejected_before_upstream_io(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = _body({"input": [{"role": [], "content": "private-current-prompt"}]})
        with running_proxy([]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as response:
                assert response.code == 400
                raw = response.read()
            error = json.loads(raw)["error"]
            assert error["code"] == "provider_portable_projection_rejected"
            assert error["reason"] == "invalid_message_role"
            assert b"private-current-prompt" not in raw
            assert received == []
        assert "Traceback" not in capsys.readouterr().err

    def test_catalog_http_error_is_relayed_once_without_responses_recovery(
        self,
    ) -> None:
        payload = b'{"error":{"type":"catalog_denied"}}'
        with running_proxy([(403, payload)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, path=upstream_path("models"), method="GET")
            with raised.value as error:
                assert error.code == 403
                assert error.read() == payload
        assert received == [b""]

    def test_exhausted_503_cooldown_is_origin_scoped_over_loopback(self) -> None:
        unavailable = _body({"error": {"type": "server_error", "code": "overloaded"}})
        success = b'{"id":"resp_other","status":"completed"}'
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([*[(503, unavailable)] * 4, (200, success)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as first:
                request(port, body, path=upstream_path(base="/a/v1"))
            with first.value as error:
                assert error.code == 503
                assert error.read() == unavailable

            with pytest.raises(urllib.error.HTTPError) as second:
                request(port, body, path=upstream_path(base="/a/v1"))
            with second.value as error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "5"
                assert json.loads(error.read())["error"]["code"] == "provider_unavailable_cooldown"

            with request(port, body, path=upstream_path(base="/b/v1")) as response:
                assert response.status == 200
                assert response.read() == success

        assert len(received) == 5

    @pytest.mark.parametrize(
        "headers",
        [{"Origin": "https://evil.example"}, {"Host": "evil.example"}],
        ids=["origin", "host"],
    )
    def test_data_request_from_a_web_page_is_refused(self, headers: dict[str, str]) -> None:
        body = _body({"input": "hello"})
        with running_proxy([(200, b"{}")]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body, path=upstream_path(), headers=headers)
            with raised.value as error:
                assert error.code == 403
            assert received == []

    def test_cooldowns_are_independent_per_origin(self) -> None:
        limited = _body({"error": {"type": "rate_limit_error", "code": "rate_limited"}})
        success = b'{"id":"resp_other","status":"completed"}'
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([(429, limited), (200, success)]) as (port, received):
            first, second = upstream_path(base="/a/v1"), upstream_path(base="/b/v1")
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body, path=first)
            with raised.value as error:
                assert error.code == 429
            with request(port, body, path=second) as response:
                assert response.read() == success
            limited_origin = cooldown.rate_limit_key(upstream_origin(base="/a/v1"))
            other_origin = cooldown.rate_limit_key(upstream_origin(base="/b/v1"))
            assert cooldown.has_failure_for_test(limited_origin)
            assert not cooldown.has_failure_for_test(other_origin)

        assert len(received) == 2

    def test_model_catalog_is_relayed_through_the_upstream_path(self) -> None:
        payload = b'{"object":"list","data":[]}'
        captures: list[dict[str, object]] = []
        with running_proxy([(200, payload)], captures=captures) as (port, received):
            path = upstream_path("models", query="client_version=0.172")
            with request(port, path=path, method="GET") as response:
                assert response.read() == payload
        assert received == [b""]
        assert [capture["method"] for capture in captures] == ["GET"]
        assert [capture["path"] for capture in captures] == ["/v1/models?client_version=0.172"]

    def test_http_477_is_relayed_once_without_retry_or_cooldown(self) -> None:
        empty = _body(
            {
                "error": {
                    "message": "the upstream returned an empty response",
                    "type": "upstream_error",
                    "code": "empty_response",
                }
            }
        )
        success = b'{"id":"resp_next","status":"completed"}'
        body = _body({"input": [{"type": "message", "role": "user", "content": "same"}]})

        with running_proxy([(477, empty), (200, success)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 477
                assert error.read() == empty
            assert len(received) == 1
            with request(port, body) as response:
                assert response.read() == success

        assert len(received) == 2
