"""Input recovery contracts."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import projection as rewrite
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import cooldown
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import telemetry
from tests.relay.fixtures import EXACT_ERROR
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import request
from tests.relay.fixtures import request_body
from tests.relay.fixtures import running_proxy

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = provider_registry.load()


class TestInputRecovery(InputTransportFixture):
    def test_exact_error_does_not_recover_a_delivery_without_its_original_call(self) -> None:
        body = json.dumps(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
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
            },
            separators=(",", ":"),
        ).encode()

        with running_proxy(
            [(400, EXACT_ERROR), (200, b'{"id":"unexpected","status":"completed"}')]
        ) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 400
                assert error.read() == EXACT_ERROR

        assert len(received) == 1

    def test_response_failed_dialogue_does_not_repeat_an_identical_compacted_attempt(self) -> None:
        body = json.dumps(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
                    {"type": "message", "role": "user", "content": "current request"},
                ]
            },
            separators=(",", ":"),
        ).encode()
        failed = b'{"error":{"type":"server_error","code":"response_failed"}}'

        with running_proxy(
            [(400, failed), (400, failed), (200, b'{"id":"unexpected","status":"completed"}')]
        ) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 503
                assert (
                    json.loads(error.read())["error"]["code"]
                    == "response_failed_recovery_exhausted"
                )

        assert len(received) == 2
        assert len(received[1]) < len(received[0])

    def test_exact_error_after_compaction_does_not_repeat_a_nonreducing_attempt(self) -> None:
        current = [
            {"type": "message", "role": "user", "content": "current request"},
            {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": "required result"},
        ]
        body = json.dumps(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 1000},
                    *current,
                ]
            },
            separators=(",", ":"),
        ).encode()
        failed = b'{"error":{"type":"server_error","code":"response_failed"}}'
        success = b'{"id":"unexpected_retry","status":"completed"}'

        with running_proxy([(400, failed), (400, EXACT_ERROR), (200, success)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 400
                assert error.read() == EXACT_ERROR

        assert len(received) == 2
        assert json.loads(received[1])["input"] == current

    @pytest.mark.parametrize(
        "required",
        [
            pytest.param(
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.invalid/current.png",
                            "detail": "high",
                        },
                        {"type": "input_text", "text": "Explain this current image"},
                    ],
                },
                id="current-image",
            ),
            pytest.param(
                {
                    "type": "agent_message",
                    "author": "/root",
                    "recipient": "/root/reviewer",
                    "content": [
                        {"type": "input_text", "text": "Review the complete task"},
                        {"type": "encrypted_content", "encrypted_content": "fixture-task"},
                    ],
                },
                id="encrypted-delegation",
            ),
        ],
    )
    def test_exact_error_recovery_preserves_required_current_turn_input(
        self, required: dict[str, object]
    ) -> None:
        success = b'{"id":"resp_recovered","status":"completed"}'
        current = {"type": "message", "role": "user", "content": "current request"}
        body = json.dumps(
            {
                "input": [
                    {"type": "message", "role": "user", "content": "old" * 100},
                    current,
                    required,
                ]
            },
            separators=(",", ":"),
        ).encode()
        with (
            running_proxy([(400, EXACT_ERROR), (200, success)]) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            assert response.read() == success

        assert len(received) == 2
        assert required in json.loads(received[0])["input"]
        recovered = json.loads(received[1])["input"]
        assert recovered == ([required] if required["type"] == "message" else [current, required])
        assert len(received[1]) < len(received[0])

    def test_exact_error_recovers_once_with_fresh_content_length(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = request_body()
        with (
            running_proxy([(400, EXACT_ERROR), (200, success)]) as (port, received),
            request(port, body) as response,
        ):
            assert response.status == 200
            assert response.read() == success

        logs = capsys.readouterr().err
        assert len(received) == 2
        recovery = received[1]
        assert len(recovery) < len(body)
        assert (
            f"event=input_variant_dialogue_recovery_accepted provider=dmxapi "
            f"bytes={len(received[0])}->{len(recovery)} "
            "retained_messages=2 dropped_input_items=1 "
        ) in logs
        recovered = json.loads(recovery)
        assert recovered["store"] is False
        assert recovered["instructions"] == "top-level-current-policy"
        assert "previous_response_id" not in recovered
        assert "conversation" not in recovered
        assert "prompt_cache_key" not in recovered
        assert recovered["include"] == ["other"]
        assert recovered["input"] == [
            {"type": "message", "role": "developer", "content": "current policy"},
            {"type": "message", "role": "user", "content": "private-current-prompt"},
        ]
        status = self._status_snapshot()
        counters = cast("dict[str, int]", status["counters"])
        classifications = cast("dict[str, int]", status["upstream_classifications"])
        assert counters["input_variant_dialogue_recovery_attempts"] == 1
        assert counters["input_variant_dialogue_recovery_accepted"] == 1
        assert counters["input_variant_dialogue_recovery_exhausted"] == 0
        assert classifications == {"input_variant_validation_error": 1}
        public_status = json.dumps(status, sort_keys=True)
        assert not re.search(
            "private-current-prompt|top-level-current-policy|stale-response-binding|"
            "stale-conversation-binding|stale-private-cache-key",
            public_status,
        )
        assert "release" in status
        assert "serving_payload_sha256" in status
        assert "release_receipt_sha256" in status

    def test_exact_error_without_a_strictly_smaller_recovery_is_passed_through(
        self,
    ) -> None:
        body = json.dumps(
            {"input": [{"type": "message", "role": "user", "content": "current"}]},
            separators=(",", ":"),
        ).encode()
        with running_proxy([(400, EXACT_ERROR)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 400
                assert error.read() == EXACT_ERROR

        assert len(received) == 1
        assert json.loads(received[0]) == {**json.loads(body), "store": False}
        counters, classifications = self._status_maps()
        assert counters["input_variant_dialogue_recovery_attempts"] == 0
        assert counters["response_failed_compaction_attempts"] == 0
        assert counters["response_failed_dialogue_recovery_attempts"] == 0
        assert classifications == {"input_variant_validation_error": 1}

    def test_top_level_error_metadata_still_admits_the_exact_contract(self) -> None:
        exact_with_metadata = json.loads(EXACT_ERROR)
        exact_with_metadata.update(
            {
                "request_id": "opaque-upstream-request-id",
                "provider": "opaque-envelope-metadata",
            }
        )
        response_body = json.dumps(exact_with_metadata, separators=(",", ":")).encode()
        success = b'{"id":"resp_recovered","status":"completed"}'
        body = request_body()
        with running_proxy([(400, response_body), (200, success)]) as (port, received):
            with request(port, body) as response:
                assert response.status == 200
                assert response.read() == success
            logs = Path(operational_log.LOG_PATH).read_text(encoding="utf-8")

        assert len(received) == 2
        assert "opaque-upstream-request-id" not in logs
        assert "opaque-envelope-metadata" not in logs
        counters, classifications = self._status_maps()
        assert counters["input_variant_dialogue_recovery_attempts"] == 1
        assert counters["input_variant_dialogue_recovery_accepted"] == 1
        assert classifications == {"input_variant_validation_error": 1}

    def test_unknown_validation_error_is_passed_through_without_retry(self) -> None:
        unknown = json.dumps(
            {
                "error": {
                    "message": "invalid request body: another schema contract",
                    "type": "invalid_request_error",
                    "param": "input",
                    "code": "validation_error",
                }
            },
            separators=(",", ":"),
        ).encode()
        body = request_body()
        with running_proxy([(400, unknown)]) as (port, received):
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                assert error.code == 400
                assert error.headers["Content-Length"] == str(len(unknown))
                assert error.read() == unknown

        assert len(received) == 1
        forwarded = json.loads(received[0])
        expected = json.loads(cast("bytes", rewrite.sanitize_responses_body(body).body))
        assert forwarded == expected
        counters, classifications = self._status_maps()
        assert counters["input_variant_dialogue_recovery_attempts"] == 0
        assert classifications == {"http_400": 1}

    def test_recovery_secondhttp_error_is_terminal_without_other_recovery(self, subtests) -> None:
        terminal_errors = {
            "classified-477": (
                477,
                b'{"error":{"message":"official provider returned an empty response",'
                b'"type":"dmx_api_error","code":"empty_response"}}',
                "wire_policy_failure",
            ),
            "response-failed": (
                400,
                b'{"error":{"message":"OpenAI responses stream failed: response_failed",'
                b'"type":"new_api_error","code":"response_failed"}}',
                "response_failed",
            ),
            "ordinary-retry": (
                503,
                b'{"error":{"message":"temporary outage","code":"upstream_failure"}}',
                "http_503_full",
            ),
            "same-exact-error": (400, EXACT_ERROR, "input_variant_validation_error"),
        }
        for label, (
            status_code,
            terminal_body,
            classification,
        ) in terminal_errors.items():
            with subtests.test(label=label):
                admission.reset_for_test()
                telemetry.reset_for_test()
                cooldown.reset_for_test()
                body = request_body()
                with running_proxy([(400, EXACT_ERROR), (status_code, terminal_body)]) as (
                    port,
                    received,
                ):
                    with pytest.raises(urllib.error.HTTPError) as raised:
                        request(port, body)
                    with raised.value as error:
                        assert error.code == status_code
                        assert error.headers["Content-Length"] == str(len(terminal_body))
                        assert error.read() == terminal_body

                assert len(received) == 2
                counters, classifications = self._status_maps()
                assert counters["input_variant_dialogue_recovery_attempts"] == 1
                assert counters["input_variant_dialogue_recovery_exhausted"] == 1
                assert counters["wire_failure_retry_attempts"] == 0
                assert counters["response_failed_compaction_attempts"] == 0
                assert counters["response_failed_dialogue_recovery_attempts"] == 0
                assert counters["streams_pre_content_reconnect_attempts"] == 0
                expected_classifications = {
                    classification: 1,
                    "input_variant_validation_error": 1,
                }
                expected_classifications[classification] += (
                    classification == "input_variant_validation_error"
                )
                assert classifications == expected_classifications

    def test_recovery_transport_failure_is_terminal_without_normal_retry(self, *, mocker) -> None:
        body = request_body()
        transport_error = urllib.error.URLError("private-upstream-detail")
        with running_proxy([(400, EXACT_ERROR)]) as (port, received):
            real_urlopen = upstream_exchange.urlopen_direct
            calls = 0

            def fail_second(outbound: urllib.request.Request, timeout: float, *, deadline: float):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise transport_error
                return real_urlopen(outbound, timeout, deadline=deadline)

            mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=fail_second)
            with pytest.raises(urllib.error.HTTPError) as raised:
                request(port, body)
            with raised.value as error:
                payload = json.loads(error.read())
                assert error.code == 502
                assert error.headers["Content-Length"] == str(
                    len(json.dumps(payload, separators=(",", ":")).encode())
                )
                assert payload["error"]["code"] == "input_variant_recovery_transport_error"
            logs = Path(operational_log.LOG_PATH).read_text(encoding="utf-8")

        assert calls == 2
        assert len(received) == 1
        assert "private-upstream-detail" not in logs
        assert "private-current-prompt" not in logs
        assert "stale-private-cache-key" not in logs
        assert "exception=URLError" in logs
        assert "event=upstream_transport_retry" not in logs
        counters, _classifications = self._status_maps()
        assert counters["input_variant_dialogue_recovery_exhausted"] == 1
