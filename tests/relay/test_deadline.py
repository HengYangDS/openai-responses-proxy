"""One upstream exchange budget survives retries, headers and body reads."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from types import SimpleNamespace
from typing import override

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import http_error


class TimedResponse(io.BytesIO):
    """Model an upstream socket whose reads consume its armed idle timeout."""

    def __init__(self, clock, chunks: list[bytes | BaseException], content_type: str) -> None:
        super().__init__()
        self.clock = clock
        self.chunks = iter(chunks)
        self.timeout = 0.7
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.status = 200
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self))

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    @override
    def read1(self, amount: int | None = -1) -> bytes:
        del amount
        chunk = next(self.chunks, b"")
        if isinstance(chunk, BaseException):
            raise chunk
        if not chunk:
            return b""
        delay = 0.2
        self.clock[0] += min(delay, self.timeout)
        if self.timeout < delay - 1e-9:
            raise TimeoutError("private upstream read timeout")
        return chunk

    @override
    def read(self, amount: int | None = -1) -> bytes:
        if amount is not None and amount >= 0:
            return self.read1(amount)
        chunks = []
        while chunk := self.read1():
            chunks.append(chunk)
        return b"".join(chunks)


class TestExchangeDeadline(InputTransportFixture):
    def test_expired_stream_cannot_complete_an_unterminated_buffered_event(self, *, mocker) -> None:
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        upstream = TimedResponse(now, [], "text/event-stream")
        chunks = iter([b'data: {"type":"response.completed"}', b"\n\n"])

        def open_headers(_request, timeout, *, deadline):
            del timeout, deadline
            now[0] += 0.4
            return upstream

        def late_read(_amount):
            chunk = next(chunks, b"")
            now[0] += 0.1 if chunk.startswith(b"data:") else 0.4
            return chunk

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        mocker.patch.object(upstream, "read1", side_effect=late_read)
        handler = MemoryHandler(b'{"input":"current","stream":true}')

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize("committed", [False, True])
    def test_late_stream_completion_cannot_commit_or_complete_an_expired_exchange(
        self, committed: bool, *, mocker
    ) -> None:
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        completed = b'data: {"type":"response.completed"}\n\n'
        upstream = TimedResponse(now, [], "text/event-stream")
        chunks = iter([content, completed] if committed else [completed])

        def open_headers(_request, timeout, *, deadline):
            del timeout, deadline
            now[0] += 0.4
            return upstream

        def late_read(_amount):
            chunk = next(chunks, b"")
            now[0] += 0.2 if chunk == content else 0.4
            return chunk

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        mocker.patch.object(upstream, "read1", side_effect=late_read)
        handler = MemoryHandler(b'{"input":"current","stream":true}')

        responses.relay(handler, "POST", provider_registry.load())

        if committed:
            assert handler.statuses == [200]
            assert handler.output() == b"%X\r\n%s\r\n" % (len(content), content)
            assert handler.close_connection
        else:
            assert handler.statuses == [504]
            assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0

    def test_success_headers_arriving_after_expiry_release_the_socket(self, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.5)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        upstream = TimedResponse(now, [], "application/json")

        def open_headers(_request, timeout, *, deadline):
            del deadline
            now[0] += timeout
            return upstream

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [504]
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0

    def test_overslept_recovery_never_starts_another_request(self, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        attempted = []

        def open_headers(_request, timeout, *, deadline):
            del deadline
            attempted.append(timeout)
            raise OSError("private connection failure")

        def oversleep(_delay):
            now[0] += 0.8

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        mocker.patch.object(upstream_exchange.time, "sleep", side_effect=oversleep)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert attempted == pytest.approx([0.7])
        assert handler.statuses == [504]
        assert self._status_snapshot()["active_responses"] == 0

    def test_expired_body_budget_refuses_even_buffered_bytes(self, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        response = TimedResponse(now, [b"never read"], "application/json")

        with pytest.raises(upstream_transport.ExchangeDeadlineError):
            upstream_transport.read_body(response, 100.0, 64)

        assert next(response.chunks) == b"never read"

    def test_http_error_arriving_at_expiry_is_closed_without_reading_or_retry(self, *, mocker):
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        upstream = TimedResponse(now, [], "application/json")

        def open_headers(_request, timeout, *, deadline):
            del deadline
            now[0] += timeout
            raise urllib.error.HTTPError(
                "https://upstream.invalid/v1/responses", 400, "Rejected", upstream.headers, upstream
            )

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [504]
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize(
        ("chunks", "reason"),
        [
            pytest.param([b"x" * 65], "response_too_large", id="oversized"),
            pytest.param(
                [ConnectionResetError("private error body")], "ConnectionResetError", id="reset"
            ),
        ],
    )
    def test_unreadable_error_body_is_a_bounded_local_failure(self, chunks, reason, *, mocker):
        mocker.patch.object(upstream_exchange.downstream, "MAX_RESPONSES_JSON_BYTES", 64)
        upstream = TimedResponse([0.0], chunks, "application/json")
        closed = mocker.spy(upstream, "close")
        error = urllib.error.HTTPError(
            "https://upstream.invalid/v1/responses", 400, "Rejected", upstream.headers, upstream
        )
        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=error)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [502]
        payload = json.loads(handler.output())["error"]
        assert payload["code"] == "upstream_error_body_unreadable"
        assert payload["reason"] == reason
        assert "private" not in handler.output().decode()
        closed.assert_called_once_with()
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize("failure", ["transport", "http"])
    def test_retry_delay_cannot_start_an_attempt_outside_the_total_budget(
        self, failure: str, *, mocker
    ) -> None:
        now = [100.0]
        attempts = []
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.5)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])

        def open_headers(_request, timeout, *, deadline):
            del deadline
            attempts.append(timeout)
            now[0] += 0.2
            if failure == "transport":
                raise OSError("private transport failure")
            raise http_error(500, "Failed", b'{"error":{"code":"server_error"}}')

        def sleep(delay):
            now[0] += delay

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        mocker.patch.object(upstream_exchange.time, "sleep", side_effect=sleep)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert attempts == pytest.approx([0.5])
        assert now[0] - 100.0 <= 0.5
        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert "private" not in handler.output().decode()
        assert self._status_snapshot()["active_responses"] == 0

    @pytest.mark.parametrize("status", [200, 400])
    def test_header_time_is_not_refunded_to_json_or_error_body_reads(
        self, status: int, *, mocker
    ) -> None:
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        upstream = TimedResponse(
            now,
            [b'{"id":"resp_budget",', b'"status":"completed",', b'"output":[]}'],
            "application/json",
        )

        def open_headers(_request, timeout, *, deadline):
            del deadline
            upstream.settimeout(timeout)
            now[0] += 0.4
            if status == 400:
                raise urllib.error.HTTPError(
                    "https://upstream.invalid/v1/responses",
                    400,
                    "Rejected",
                    upstream.headers,
                    upstream,
                )
            return upstream

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(b'{"input":"current"}', path="/ucloud/v1/responses")

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert now[0] - 100.0 == pytest.approx(0.7)
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0

    def test_committed_stream_uses_the_original_exchange_deadline_without_replay(
        self, *, mocker
    ) -> None:
        now = [100.0]
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(sse, "UPSTREAM_TIMEOUT", 0.7)
        mocker.patch.object(upstream_exchange.time, "monotonic", side_effect=lambda: now[0])
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        completed = b'data: {"type":"response.completed"}\n\n'
        upstream = TimedResponse(now, [content, completed], "text/event-stream")
        attempts = []

        def open_headers(_request, timeout, *, deadline):
            del deadline
            attempts.append(timeout)
            upstream.settimeout(timeout)
            now[0] += 0.4
            return upstream

        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)
        handler = MemoryHandler(b'{"input":"current","stream":true}')

        responses.relay(handler, "POST", provider_registry.load())

        assert handler.statuses == [200]
        assert handler.output() == b"%X\r\n%s\r\n" % (len(content), content)
        assert handler.close_connection
        assert attempts == pytest.approx([0.7])
        assert now[0] - 100.0 == pytest.approx(0.7)
        assert upstream.closed
        assert self._status_snapshot()["active_responses"] == 0
