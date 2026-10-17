"""One-shot empty-response recovery and terminal attempt budgets."""

from __future__ import annotations

import json
import tempfile
from typing import cast

from openai_responses_proxy.providers.policies import dmxapi as policy
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.serving import entrypoint as proxy
from tests.relay.empty_response.fixtures import EMPTY_RESPONSE
from tests.relay.empty_response.fixtures import SUCCESS
from tests.relay.empty_response.fixtures import UNKNOWN_477
from tests.relay.empty_response.fixtures import EmptyResponseFixture
from tests.relay.fixtures import serve_proxy


class EmptyResponseTransportTests(EmptyResponseFixture):
    def test_fallback_failure_is_terminal_after_exactly_two_upstream_attempts(self, subtests):
        body = self._body(
            {
                "stream": False,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        cases = (
            (400, b'{"error":{"code":"bad_request"}}'),
            (477, EMPTY_RESPONSE),
            (500, b'{"error":{"code":"upstream_failure"}}'),
        )
        for second_status, second_payload in cases:
            with subtests.test(second_status=second_status):
                admission.reset_for_test()
                telemetry.reset_for_test()
                cooldown.reset_for_test()
                responses = [(477, EMPTY_RESPONSE), (second_status, second_payload)]
                responses += [(second_status, second_payload)] * (3 * (second_status in (477, 500)))
                with tempfile.TemporaryDirectory() as tmp:
                    port, received, cleanup = serve_proxy(responses, tmp)
                    try:
                        code, _headers, raw = self._read_http_error(port, body)
                    finally:
                        cleanup()
                assert code == 503
                assert len(received) == 2
                assert json.loads(raw)["error"]["attempts"] == 2

    def test_unknown_477_is_passed_through_without_fallback_or_cooldown(self):
        body = self._body({"stream": False, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, UNKNOWN_477)], tmp)
            try:
                code, _headers, raw = self._read_http_error(port, body)
            finally:
                cleanup()

        assert code == 477
        assert raw == UNKNOWN_477
        projected = self._body({"stream": False, "input": [], "store": False})
        assert received == [projected]
        key = policy.request_fingerprint(projected)
        assert self._cooldown_remaining(key, now=100.0) == 0

    def test_streaming_request_recovers_only_before_any_sse_bytes(self):
        body = self._body(
            {
                "stream": True,
                "previous_response_id": "remove-on-fallback",
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        recovered = {
            "chunks": [
                b'data: {"type":"response.created"}\n\n',
                b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
                b'data: {"type":"response.completed"}\n\n',
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE), recovered], tmp)
            try:
                with self._request(port, body) as response:
                    payload = response.read()
            finally:
                cleanup()

        assert response.status == 200
        assert b'"delta":"ok"' in payload
        assert len(received) == 2
        assert received[1] != body

    def test_streaming_fallback_exhaustion_is_standard_http_503(self):
        body = self._body({"stream": True, "input": []})

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy([(477, EMPTY_RESPONSE)] * 4, tmp)
            try:
                code, headers, raw = self._read_http_error(port, body)
            finally:
                cleanup()

        assert code == 503
        assert headers["Content-Type"] == "application/json"
        assert headers["Retry-After"] == "3"
        assert json.loads(raw)["error"]["attempts"] == 2
        assert len(received) == 2

    def test_dedicated_slot_survives_ordinary_transient_retry_budget(self):
        body = self._body(
            {
                "stream": False,
                "input": [{"type": "message", "role": "user", "content": "hello"}],
            }
        )
        transient = b'{"error":{"code":"upstream_failure"}}'
        responses = [
            (500, transient),
            (500, transient),
            (500, transient),
            (477, EMPTY_RESPONSE),
            (200, SUCCESS),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy(responses, tmp)
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        assert len(received) == 5
        status = proxy.runtime_status()
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_attempts"] == 1
        assert cast("dict[str, int]", status["counters"])["wire_failure_retry_accepted"] == 1

    def test_fallback_follows_response_failed_recovery_without_metric_overlap(self):
        error = b'{"error":{"type":"new_api_error","code":"response_failed"}}'
        body = self._body(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "x" * 100_000},
                    {"type": "message", "role": "user", "content": "latest"},
                ]
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            port, received, cleanup = serve_proxy(
                [(400, error), (477, EMPTY_RESPONSE), (200, SUCCESS)], tmp
            )
            try:
                with self._request(port, body) as response:
                    assert response.read() == SUCCESS
            finally:
                cleanup()

        counters = cast("dict[str, int]", proxy.runtime_status()["counters"])
        assert len(received) == 3
        assert len(received[1]) < len(received[0])
        assert received[2] == received[1]
        assert counters["response_failed_compaction_attempts"] == 1
        assert counters["response_failed_compaction_accepted"] == 0
        assert counters["wire_failure_retry_attempts"] == 1
        assert counters["wire_failure_retry_accepted"] == 1
