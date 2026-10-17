"""One stream deadline covers backoff, reconnect headers, and response reads."""

from __future__ import annotations

import pytest

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import downstream
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import sse
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler

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
        mocker.patch.object(sse.time, "monotonic", side_effect=lambda: now[0])

        def sleep(delay: float) -> None:
            now[0] += delay

        def open_headers(_request: object, timeout: float) -> DirectResponse:
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
            assert handler.statuses == [503]
            assert b"stream_pre_content_exhausted" in handler.output()
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
