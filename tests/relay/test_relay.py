"""End-to-end HTTP and SSE behavior through real loopback proxy hops."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy

ROOT = Path(__file__).resolve().parents[2]


class TestProxyTransport:
    """Exercise retry behavior through real local HTTP hops."""

    def setup_method(self):
        from openai_responses_proxy.serving import entrypoint as p

        self.p = p
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()

    def exchange(self, scripted, body):
        """Run one loopback exchange and return received requests and payload."""
        with (
            running_proxy(scripted) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            return received, response.read()

    def test_same_provider_requests_overlap_without_a_proxy_concurrency_gate(self):
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        success = b'{"id":"resp_concurrent","status":"completed"}'
        scripted = [
            {
                "status": 200,
                "content_type": "application/json",
                "chunks": [success],
                "started_event": event,
                "release_event": release,
            }
            for event in started
        ]
        results: list[tuple[int, bytes]] = []
        body = b'{"model":"gpt-5.6-terra","stream":false,"input":[]}'

        def send(port: int) -> None:
            with request(port, body, path="/aihubmix/v1/responses") as response:
                results.append((response.status, response.read()))

        with running_proxy(scripted) as (port, received):
            workers = [threading.Thread(target=send, args=(port,)) for _ in started]
            for worker in workers:
                worker.start()
            try:
                assert all(event.wait(2) for event in started)
            finally:
                release.set()
            for worker in workers:
                worker.join(timeout=2)
                assert not worker.is_alive()

        assert len(received) == 2
        assert results == [(200, success), (200, success)]

    def test_recovers_response_failed_with_pair_safe_compact_request(self):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_old",
                        "name": "tool",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_old",
                        "output": "old result",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        received, payload = self.exchange([(400, response_failed), (200, success)], body)
        assert payload == success

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 2
        compact = json.loads(received[1])
        assert len(received[1]) < len(body)
        assert "prompt_cache_key" not in compact
        assert compact["input"][-1]["content"] == "latest user context"
        call_types = {"custom_tool_call", "function_call"}
        output_types = {"custom_tool_call_output", "function_call_output"}
        calls = {item["call_id"] for item in compact["input"] if item.get("type") in call_types}
        outputs = {item["call_id"] for item in compact["input"] if item.get("type") in output_types}
        assert outputs.issubset(calls)

    def test_recovers_blocked_invalid_prompt_with_pair_safe_compact_request(self):
        blocked = (
            b'{"error":{"message":"Request blocked. (request id: fixture)",'
            b'"type":"invalid_request_error","param":"","code":"invalid_prompt"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {"type": "message", "role": "developer", "content": "policy"},
                    {
                        "type": "message",
                        "role": "user",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_old",
                        "name": "tool",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_old",
                        "output": "old result",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        received, payload = self.exchange([(400, blocked), (200, success)], body)
        assert payload == success

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 2
        compact = json.loads(received[1])
        assert len(received[1]) < len(body)
        assert "prompt_cache_key" not in compact
        assert compact["input"][-1]["content"] == "latest user context"
        classifications = cast(
            "dict[str, int]", self.p.runtime_status()["upstream_classifications"]
        )
        assert classifications.get("blocked_invalid_prompt") == 1
        assert "response_failed" not in classifications

    def test_passes_through_unrelated_invalid_prompt(self):
        invalid_prompt = (
            b'{"error":{"message":"caller supplied an unsupported option",'
            b'"type":"invalid_request_error","param":"tool_choice","code":"invalid_prompt"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        with running_proxy([(400, invalid_prompt)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value:
                assert raised.value.code == 400
                assert raised.value.read() == invalid_prompt

        assert json.loads(received[0]) == {**json.loads(body), "store": False}

    def test_rejected_encrypted_delegation_keeps_its_required_task_payload(self):
        rejection = b'{"error":{"type":"invalid_request_error","code":"invalid_encrypted_content"}}'
        request_body = json.dumps(
            {
                "model": "gpt-6-astra",
                "stream": False,
                "input": [
                    {
                        "type": "agent_message",
                        "author": "/root",
                        "recipient": "/root/reviewer",
                        "content": [
                            {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                            {"type": "encrypted_content", "encrypted_content": "task-payload"},
                        ],
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()

        with running_proxy(
            [(400, rejection), (200, b'{"id":"resp_unrelated","status":"completed","output":[]}')]
        ) as (
            port,
            received,
        ):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, request_body)
            with raised.value:
                assert raised.value.code == 400
                assert raised.value.read() == rejection

        assert len(received) == 1
        assert b"task-payload" in received[0]
        snapshot = telemetry.status(
            release="test",
            serving_payload_sha256=None,
            release_receipt_sha256=None,
            admission={},
            runtime_identity={},
        )
        classifications = cast("dict[str, int]", snapshot["upstream_classifications"])
        assert classifications["provider_bound_encrypted_content"] == 1

    def test_recovers_response_failed_with_dialogue_only_last_resort(self, *, mocker):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "prompt_cache_key": "full-history-cache-key",
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "old" + "x" * 100_000,
                    },
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current policy",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_new",
                        "name": "tool",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_new",
                        "output": "tool result",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 1)
        log = mocker.patch.object(operational_log, "log")

        with (
            running_proxy([(400, response_failed), (400, response_failed), (200, success)]) as (
                port,
                received,
            ),
            request(port, body) as response,
        ):
            assert response.status == 200
            assert response.read() == success
        logs = "\n".join(call.args[0] for call in log.call_args_list)

        assert received[0] == rewrite.sanitize_responses_body(body).body
        assert len(received) == 3
        recovery = json.loads(received[2])
        assert "prompt_cache_key" not in recovery
        assert recovery["input"] == [
            {"type": "message", "role": "developer", "content": "current policy"},
            {"type": "message", "role": "user", "content": "latest user context"},
        ]
        assert "event=response_failed_dialogue_recovery_accepted" in logs
        assert "event=response_failed_compact_recovery_accepted" not in logs

    def test_normalizes_exhausted_response_failed_recovery_to_retryable_503(self, *, mocker):
        response_failed = (
            b'{"error":{"message":"OpenAI responses stream failed: '
            b'response_failed - Response failed",'
            b'"type":"new_api_error","code":"response_failed"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "current policy",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "latest user context",
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_new",
                        "name": "tool",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_new",
                        "output": "tool result",
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange, "RESPONSE_FAILED_MAX_STAGES", 0)

        with running_proxy([(400, response_failed), (400, response_failed)]) as (
            port,
            received,
        ):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert len(received) == 1
        assert payload["error"]["code"] == "response_failed_recovery_exhausted"

    def test_retries_classified_empty_response_at_most_once_with_unchanged_body(self, *, mocker):
        empty_response = (
            b'{"error":{"message":"official provider returned an empty response",'
            b'"type":"dmx_api_error","code":"empty_response"}}'
        )
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        received, payload = self.exchange([(477, empty_response), (200, success)], body)
        assert payload == success

        assert received[0] == received[1]
        assert json.loads(received[0]) == {**json.loads(body), "store": False}

    def test_runtime_metrics_classify_recovery_without_retaining_request_content(self):
        response_failed = b'{"error":{"type":"new_api_error","code":"response_failed"}}'
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = json.dumps(
            {
                "stream": False,
                "input": [
                    {"type": "reasoning", "encrypted_content": "secret-replay"},
                    {"type": "message", "role": "user", "content": "old context"},
                    {"type": "message", "role": "user", "content": "x" * 100_000},
                    {"type": "message", "role": "user", "content": "private prompt"},
                ],
            },
            separators=(",", ":"),
        ).encode()

        _received, payload = self.exchange([(400, response_failed), (200, success)], body)
        assert payload == success

        status = self.p.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        classifications = cast("dict[str, int]", status["upstream_classifications"])
        assert counters["responses_received"] == 1
        assert counters["encrypted_replayed_reasoning_items_stripped"] == 1
        assert counters["response_failed_compaction_attempts"] == 1
        assert counters["response_failed_compaction_accepted"] == 1
        assert classifications["response_failed"] == 1
        assert "private prompt" not in json.dumps(status)
        assert "secret-replay" not in json.dumps(status)

    def test_loopback_healthz_returns_machine_readable_metrics(self):
        with (
            running_proxy([]) as (port, _received),
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/healthz"
            ) as response,
        ):
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json"
            status = json.loads(response.read())

        assert "counters" in status
        assert "upstream_classifications" in status
        assert status["last_failure"] is None

    def test_loopback_drain_rejects_new_responses_and_can_be_reopened(self):
        success = b'{"id":"resp_served","status":"completed"}'
        body = json.dumps({"stream": False, "input": []}).encode()
        with running_proxy([(200, success)]) as (port, received):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            drain = urllib.request.Request(
                f"http://127.0.0.1:{port}/control/drain",
                method="POST",
            )
            with opener.open(drain) as response:
                snapshot = json.loads(response.read())
            assert snapshot["draining"]
            assert snapshot["active_responses"] == 0

            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value:
                assert raised.value.code == 503
                assert raised.value.headers["Retry-After"] == "1"
                payload = json.loads(raised.value.read())
            assert payload["error"]["code"] == "proxy_draining"
            assert received == []

            reopen = urllib.request.Request(
                f"http://127.0.0.1:{port}/control/drain",
                method="DELETE",
            )
            with opener.open(reopen) as response:
                assert not json.loads(response.read())["draining"]
            with request(port, body) as response:
                assert response.read() == success

        status = self.p.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["responses_rejected_while_draining"] == 1
        assert not status["draining"]

    def test_drain_lease_expires_without_a_controller_rollback_request(self, *, mocker):
        admission.reset_for_test()
        telemetry.reset_for_test()
        cooldown.reset_for_test()
        mocker.patch.object(admission.time, "monotonic", side_effect=[10.0, 10.0, 12.1, 12.1, 12.1])
        started = admission.set_draining(True, lease_seconds=2)
        expired = self.p.runtime_status()
        assert started["draining"]
        assert not expired["draining"]
        assert expired["drain_lease_remaining_seconds"] is None
        counters = cast("dict[str, int]", expired["counters"])
        assert counters["drain_leases_expired"] == 1

    def test_drain_closes_admission_while_an_existing_response_finishes(self):
        success = b'{"id":"resp_served","status":"completed"}'
        body = json.dumps({"stream": False, "input": []}).encode()
        started = threading.Event()
        release = threading.Event()
        worker_result = {}

        scripted = {
            "status": 200,
            "content_type": "application/json",
            "chunks": [success],
            "started_event": started,
            "release_event": release,
        }
        with running_proxy([scripted]) as (port, received):

            def request_in_flight():
                try:
                    with request(port, body) as response:
                        worker_result["body"] = response.read()
                except BaseException as exc:  # asserted below; never hide a worker failure
                    worker_result["error"] = exc

            worker = threading.Thread(target=request_in_flight)
            worker.start()
            try:
                assert started.wait(timeout=2), (
                    "upstream never received the first Responses request"
                )

                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                drain = urllib.request.Request(
                    f"http://127.0.0.1:{port}/control/drain",
                    method="POST",
                )
                with opener.open(drain) as response:
                    snapshot = json.loads(response.read())
                assert snapshot["draining"]
                assert snapshot["active_responses"] == 1

                with pytest.raises(urllib.error.HTTPError) as raised:
                    request(port, body)
                with raised.value:
                    assert raised.value.code == 503
                    assert json.loads(raised.value.read())["error"]["code"] == "proxy_draining"
                assert json.loads(received[0]) == {**json.loads(body), "store": False}

                release.set()
                worker.join(timeout=3)
                assert not worker.is_alive(), "in-flight request did not complete after drain"
                assert "error" not in worker_result
                assert worker_result["body"] == success

                with opener.open(f"http://127.0.0.1:{port}/healthz") as response:
                    drained = json.loads(response.read())
                assert drained["draining"]
                assert drained["active_responses"] == 0
            finally:
                release.set()

    def test_reconnects_a_pre_content_response_failed_stream(self, *, mocker):
        failed = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n',
                b"data: [DONE]\n\n",
            ],
        }
        recovered = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ],
        }
        body = json.dumps({"stream": True, "input": []}).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        received, payload = self.exchange([failed, recovered], body)

        assert len(received) == 2
        assert b"recovered" in payload
        assert payload.count(b"response.created") == 1
        status = self.p.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["streams_pre_content_reconnect_attempts"] == 1
        assert counters["streams_completed"] == 1

    def test_normalizes_exhausted_pre_content_sse_failures_to_retryable_503(self, *, mocker):
        premature_eof = {"chunks": [b'data: {"type":"response.created"}\n\n']}
        body = json.dumps({"stream": True, "input": []}).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)

        with running_proxy([premature_eof] * 6) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert len(set(received)) == 1
        assert json.loads(received[0]) == {**json.loads(body), "store": False}
        assert payload["error"]["type"] == "upstream_unavailable"
        assert payload["error"]["code"] == "stream_pre_content_exhausted"
        assert payload["error"]["attempts"] == 6
        status = self.p.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        last_failure = cast("dict[str, object]", status["last_failure"])
        assert counters["streams_pre_content_reconnect_attempts"] == 5
        assert counters["streams_pre_content_exhausted"] == 1
        assert counters["streams_failed"] == 1
        assert last_failure["classification"] == "stream_pre_content_exhausted"

    def test_reconnects_a_pre_content_premature_eof(self, *, mocker):
        premature_eof = {"chunks": [b'data: {"type":"response.created"}\n\n']}
        recovered = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ],
        }
        body = json.dumps({"stream": True, "input": []}).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)
        received, payload = self.exchange([premature_eof, recovered], body)

        assert len(received) == 2
        assert b'"delta":"ok"' in payload
        counters = cast("dict[str, int]", self.p.runtime_status()["counters"])
        assert counters["streams_pre_content_reconnect_attempts"] == 1

    def test_does_not_reconnect_after_downstream_stream_bytes_are_committed(self):
        partial = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
            ],
        }
        unexpected_retry = {
            "chunks": [b'data: {"type":"response.completed"}\n\n'],
        }
        body = json.dumps({"stream": True, "input": []}).encode()

        with (
            running_proxy([partial, unexpected_retry]) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            with pytest.raises(http.client.IncompleteRead) as raised:
                response.read()
            payload = raised.value.partial

        assert len(received) == 1
        assert b"partial" in payload
        status = self.p.runtime_status()
        counters = cast("dict[str, int]", status["counters"])
        assert counters["streams_pre_content_reconnect_attempts"] == 0
        assert counters["streams_failed"] == 1

    def test_normalizes_exhausted_classified_empty_response_to_retryable_503(self, *, mocker):
        empty_response = (
            b'{"error":{"message":"official provider returned an empty response",'
            b'"type":"dmx_api_error","code":"empty_response"}}'
        )
        body = json.dumps(
            {
                "model": "gpt-5.6-terra",
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            },
            separators=(",", ":"),
        ).encode()
        mocker.patch.object(upstream_exchange.time, "sleep", return_value=None)

        with running_proxy([(477, empty_response)] * 4) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            error = raised.value
            with error:
                assert error.code == 503
                assert error.headers["Retry-After"] == "3"
                payload = json.loads(error.read())

        assert received[0] == received[1]
        assert json.loads(received[0]) == {**json.loads(body), "store": False}
        assert payload["error"]["type"] == "upstream_unavailable"
        assert payload["error"]["code"] == "dmx_empty_response_exhausted"
        assert payload["error"]["attempts"] == 2

    def test_drops_unreplayable_images_and_keeps_text_and_https(self):
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "image-call",
                        "name": "inspect",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "image-call",
                        "output": [
                            {"type": "input_text", "text": "before"},
                            {"type": "input_image", "image_url": "/tmp/example.png"},
                            {"type": "input_text", "text": "after"},
                        ],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "https://example.test/valid.png",
                            },
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,not-supported",
                            },
                        ],
                    },
                ]
            }
        ).encode()

        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))

        assert "local_image_items=2" in note
        assert obj["input"][1]["output"] == [
            {"type": "input_text", "text": "before"},
            {"type": "input_text", "text": "after"},
        ]
        assert obj["input"][2]["content"] == [
            {"type": "input_image", "image_url": "https://example.test/valid.png"}
        ]

    def test_drops_malformed_http_like_image_urls(self):
        bad_urls = [
            "https://",
            "https://bad host/example.png",
            "http:///missing-host",
            "https://example.test:not-a-port/image.png",
            "https://example.test/has space.png",
        ]
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "image-call",
                        "name": "inspect",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "image-call",
                        "output": [{"type": "input_image", "image_url": url} for url in bad_urls]
                        + [
                            {
                                "type": "input_image",
                                "image_url": "https://example.test/valid.png",
                            },
                        ],
                    },
                ],
            }
        ).encode()

        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))

        assert f"local_image_items={len(bad_urls)}" in note
        assert obj["input"][1]["output"] == [
            {"type": "input_image", "image_url": "https://example.test/valid.png"}
        ]
