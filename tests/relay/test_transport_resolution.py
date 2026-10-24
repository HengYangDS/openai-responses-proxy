"""Native address resolution waits only within the exchange deadline."""

from __future__ import annotations

import json
import socket
import threading
import time

from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import exchange as upstream_exchange
from openai_responses_proxy.relay import responses
from tests.relay.fixtures import InputTransportFixture
from tests.relay.fixtures import MemoryHandler


class TestBoundedResolution(InputTransportFixture):
    def test_a_stalled_lookup_ends_the_exchange_at_its_deadline(self, *, mocker):
        released = threading.Event()
        hosts: list[str] = []

        def stalled(host, *args, **kwargs):
            del args, kwargs
            hosts.append(host)
            released.wait(timeout=10)
            raise socket.gaierror(socket.EAI_AGAIN, "fixture lookup released")

        mocker.patch.object(socket, "getaddrinfo", side_effect=stalled)
        mocker.patch.object(upstream_exchange, "UPSTREAM_TIMEOUT", 0.3)
        providers = provider_registry.Registry(
            {"fixture": provider_registry.Profile("fixture", "http://upstream.invalid/v1")}
        )
        handler = MemoryHandler(b'{"input":"current"}', path="/fixture/v1/responses")
        started = time.monotonic()
        try:
            responses.relay(handler, "POST", providers)
            elapsed = time.monotonic() - started
        finally:
            released.set()

        assert handler.statuses == [504]
        assert json.loads(handler.output())["error"]["code"] == "upstream_deadline_exceeded"
        assert elapsed < 1.5
        assert hosts == ["upstream.invalid"]
        assert self._status_snapshot()["active_responses"] == 0

    def test_exchanges_for_one_address_share_one_lookup(self, *, mocker):
        released = threading.Event()
        calls: list[str] = []

        def slow(host, port, *args, **kwargs):
            del args, kwargs
            calls.append(host)
            released.wait(timeout=10)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))
            ]

        mocker.patch.object(socket, "getaddrinfo", side_effect=slow)
        results: list[object] = []

        def resolve() -> None:
            results.append(
                upstream_exchange.upstream_transport.resolve(
                    "shared.invalid", 443, time.monotonic() + 5
                )
            )

        waiters = [threading.Thread(target=resolve) for _ in range(3)]
        for waiter in waiters:
            waiter.start()
        time.sleep(0.2)
        released.set()
        for waiter in waiters:
            waiter.join(timeout=5)

        assert calls == ["shared.invalid"]
        assert len(results) == 3

    def test_a_numeric_address_needs_no_lookup(self, *, mocker):
        lookup = mocker.patch.object(socket, "getaddrinfo", wraps=socket.getaddrinfo)
        threads = threading.active_count()

        addresses = upstream_exchange.upstream_transport.resolve(
            "127.0.0.1", 9, time.monotonic() + 1
        )

        assert addresses[0][4] == ("127.0.0.1", 9)
        assert lookup.call_args.args[:2] == ("127.0.0.1", 9)
        assert threading.active_count() == threads
