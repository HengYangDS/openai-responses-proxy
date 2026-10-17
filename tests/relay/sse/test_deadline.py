"""One stream deadline covers backoff, reconnect headers, and response reads."""

from __future__ import annotations

import json
import threading
import urllib.error

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import upstream as upstream_transport
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler
from tests.relay.fixtures import request
from tests.relay.fixtures import running_proxy

FAILED = b'data: {"type":"response.failed","response":{"error":{"code":"server_error"}}}\n\n'
COMPLETED = b'data: {"type":"response.completed"}\n\n'


def _exchange(handler: MemoryHandler) -> upstream_exchange.Exchange:
    resolved = provider_registry.load().resolve("/ucloud/v1/responses")
    assert resolved is not None
    profile, _, url = resolved
    return upstream_exchange.Exchange(
        handler=handler,
        method="POST",
        request_id=1,
        body=b'{"input":"current"}',
        url=url,
        headers={},
        is_responses=True,
        attempt_body=b'{"input":"current","store":false}',
        profile=profile,
    )


class TestStreamDeadline(InputTransportFixture):
    def test_typed_reconnect_deadline_is_not_a_generic_stream_failure(self, *, mocker) -> None:
        now = [100.0]
        mocker.patch.object(sse, "UPSTREAM_TIMEOUT", 1.1)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 1.1)
        mocker.patch.object(sse.time, "monotonic", side_effect=lambda: now[0])

        def sleep(delay: float) -> None:
            now[0] += delay

        mocker.patch.object(sse.time, "sleep", side_effect=sleep)
        mocker.patch.object(
            upstream_exchange,
            "urlopen_direct",
            side_effect=upstream_transport.ExchangeDeadlineError(
                "native deadline budget exhausted"
            ),
        )
        handler = MemoryHandler()

        downstream.relay_sse(_exchange(handler), DirectResponse(FAILED))

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert now[0] == 101.0

    @pytest.mark.parametrize("committed", [False, True])
    def test_typed_deadline_cannot_finish_a_buffered_terminal_event(
        self, committed: bool, *, mocker
    ) -> None:
        mocker.patch.object(sse.time, "monotonic", return_value=100.0)
        content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'
        chunks = [content] if committed else []
        upstream = DirectResponse(
            *chunks,
            b'data: {"type":"response.completed"}',
            upstream_transport.ExchangeDeadlineError("native deadline budget exhausted"),
        )
        handler = MemoryHandler()

        result = sse.relay(handler, upstream, "/v1/responses", 1, deadline=100.5)

        stream = result["result"]
        assert stream is not None
        assert stream["detail"] == "deadline"
        assert stream["terminal"] is None
        assert result["attempts"] == 1
        if committed:
            assert handler.output() == b"%X\r\n%s\r\n" % (len(content), content)
            assert handler.close_connection
        else:
            assert handler.output() == b""

    def test_real_loopback_reconnect_headers_stop_at_the_stream_deadline(self, *, mocker) -> None:
        released = threading.Event()
        second_started = threading.Event()
        release = threading.Timer(5, released.set)
        # The real journey allows header acquisition after the native one-second
        # backoff. The deterministic companion separately proves the 0.1s edge.
        mocker.patch.object(sse, "UPSTREAM_TIMEOUT", 2.0)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 2.0)
        release.start()
        try:
            with running_proxy(
                [
                    {"payload": FAILED, "content_type": "text/event-stream"},
                    {
                        "payload": b"",
                        "content_type": "text/event-stream",
                        "started_event": second_started,
                        "release_event": released,
                    },
                ]
            ) as (port, received):
                try:
                    with pytest.raises(urllib.error.HTTPError) as raised:
                        request(port, b'{"input":"current","stream":true}')
                    with raised.value as error:
                        assert error.code == 504
                        payload = json.loads(error.read())
                        assert payload["error"]["code"] == "upstream_deadline_exceeded"
                    assert second_started.is_set()
                    assert not released.is_set()
                finally:
                    released.set()
            assert len(received) == 2
        finally:
            released.set()
            release.cancel()
            release.join(timeout=6)
        assert not release.is_alive()

    @pytest.mark.parametrize("header_delay", [0.05, 0.5])
    def test_reconnect_header_acquisition_uses_the_remaining_stream_budget(
        self, header_delay: float, *, mocker
    ) -> None:
        now = [100.0]
        timeouts: list[float] = []
        handler = MemoryHandler()
        first = DirectResponse(FAILED, content_type="text/event-stream")
        successor = DirectResponse(COMPLETED, content_type="text/event-stream")
        first_close = mocker.spy(first, "close")
        successor_close = mocker.spy(successor, "close")
        mocker.patch.object(sse, "UPSTREAM_TIMEOUT", 1.1)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 1.1)
        mocker.patch.object(sse.time, "monotonic", side_effect=lambda: now[0])

        def sleep(delay: float) -> None:
            now[0] += delay

        def open_headers(_request: object, timeout: float, *, deadline: float) -> DirectResponse:
            del deadline
            timeouts.append(timeout)
            now[0] += min(header_delay, timeout)
            if header_delay > timeout:
                raise TimeoutError("scripted header deadline")
            return successor

        mocker.patch.object(sse.time, "sleep", side_effect=sleep)
        mocker.patch.object(upstream_exchange, "urlopen_direct", side_effect=open_headers)

        downstream.relay_sse(_exchange(handler), first)

        assert now[0] - 100.0 <= 1.1 + 1e-9
        assert timeouts == pytest.approx([0.1])
        first_close.assert_called_once_with()
        if header_delay < 0.1:
            assert handler.statuses == [200]
            assert COMPLETED in handler.output()
            successor_close.assert_called_once_with()
        else:
            assert handler.statuses == [504]
            assert b"upstream_deadline_exceeded" in handler.output()
            successor_close.assert_not_called()

    def test_expiry_during_backoff_never_starts_another_upstream_attempt(self, *, mocker) -> None:
        now = [100.0]
        upstream = DirectResponse(FAILED)
        close = mocker.spy(upstream, "close")
        reopen = mocker.Mock(return_value=DirectResponse(COMPLETED))
        mocker.patch.object(sse, "UPSTREAM_TIMEOUT", 1.1)
        mocker.patch.object(sse.time, "monotonic", side_effect=lambda: now[0])

        def oversleep(delay: float) -> None:
            now[0] += delay + 0.2

        mocker.patch.object(sse.time, "sleep", side_effect=oversleep)

        result = sse.relay(MemoryHandler(), upstream, "/v1/responses", 1, reopen=reopen)

        assert result["pre_content_exhausted"]
        assert result["attempts"] == 1
        reopen.assert_not_called()
        close.assert_called_once_with()

    def test_expired_header_deadline_refuses_network_io(self, *, mocker) -> None:
        mocker.patch.object(upstream_exchange.time, "monotonic", return_value=100.0)
        open_ = mocker.patch.object(upstream_exchange, "urlopen_direct")

        with pytest.raises(TimeoutError):
            _exchange(MemoryHandler()).upstream(deadline=100.0)

        open_.assert_not_called()
