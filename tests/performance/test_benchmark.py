"""Benchmark workloads execute real product paths and own their resources."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from openai_responses_proxy.relay import operational_log
from tools.performance import benchmark


@pytest.fixture(autouse=True)
def isolated_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operational_log, "LOG_PATH", str(tmp_path / "proxy.log"))


def test_loopback_workloads_preserve_response_bytes_and_close_listeners() -> None:
    with benchmark._environment() as environment:
        assert environment.exchange() == benchmark._SMALL_RESPONSE
        assert environment.exchange("?stream=1") == benchmark._STREAM_RESPONSE
        assert environment.exchange("?large=1") == benchmark._LARGE_RESPONSE
        assert isinstance(json.loads(environment.health()), dict)
    assert not environment.proxy_thread.is_alive()
    assert not environment.upstream_thread.is_alive()
    assert environment.proxy.fileno() == -1
    assert environment.upstream.fileno() == -1


def test_first_event_latency_stops_before_the_stream_completes() -> None:
    # The upstream holds its terminal event until the client has read the first
    # one, so a measurement that waited for completion could not finish early.
    with benchmark._environment() as environment:
        before = benchmark._UpstreamHandler.terminals_without_first_event
        elapsed = environment.first_event(3)
    assert 0 < elapsed < benchmark._FIRST_EVENT_HOLD_SECONDS
    assert benchmark._UpstreamHandler.terminals_without_first_event == before


def test_workload_failure_still_closes_owned_listeners(mocker) -> None:
    environment = benchmark._Environment()
    mocker.patch.object(benchmark, "_Environment", return_value=environment)
    with pytest.raises(RuntimeError, match="injected"), benchmark._environment():
        raise RuntimeError("injected")
    assert not environment.proxy_thread.is_alive()
    assert not environment.upstream_thread.is_alive()


@pytest.mark.parametrize("method", ["exchange", "health"])
def test_workload_rejects_non_bytes_response(method: str, mocker) -> None:
    with benchmark._environment() as environment:
        response = mocker.MagicMock()
        response.__enter__.return_value.read.return_value = "not bytes"
        mocker.patch.object(environment.opener, "open", return_value=response)
        with pytest.raises(TypeError, match="must be bytes"):
            getattr(environment, method)()


@pytest.mark.parametrize("mode", ["latency", "memory"])
def test_pyperf_registrations_execute_the_declared_workloads(mode: str, mocker) -> None:
    recorded = {}

    def execute(name, function, *arguments):
        recorded[name] = function(*arguments)

    runner = mocker.Mock()
    runner.bench_func.side_effect = execute
    runner.bench_time_func.side_effect = lambda name, function, *arguments, **_options: execute(
        name, function, 1, *arguments
    )
    factory = mocker.patch.object(benchmark.pyperf, "Runner", return_value=runner)
    # Packaged startup needs the performance session's private installation;
    # its observation and its payload identity have their own tests.
    startup = mocker.patch.object(benchmark, "_startup_to_ready", return_value=0.5)
    named = {"payload_release": "9.9.9"}
    mocker.patch.object(benchmark, "_startup_metadata", return_value=named)
    if mode == "latency":
        benchmark.main()
        assert set(recorded) == {
            "provider-route-resolution",
            "responses-projection-4kib",
            "startup-to-ready",
            "handoff-control-round-trip",
            "health-status",
            "non-streaming-request",
            "streaming-first-event",
            "forwarding-1mib",
        }
        runner.bench_time_func.assert_any_call("startup-to-ready", startup, named, metadata=named)
        startup.assert_called_once_with(1, named)
        assert recorded["non-streaming-request"] == benchmark._SMALL_RESPONSE
        assert 0 < recorded["streaming-first-event"] < benchmark._FIRST_EVENT_HOLD_SECONDS
        assert recorded["handoff-control-round-trip"]["phase"] == "ready"
        factory.assert_called_once_with(program_args=("-m", "tools.performance.benchmark"))
    else:
        runpy.run_module("tools.performance.memory", run_name="__main__")
        assert set(recorded) == {"large-request-projection-1mib", "large-response-forwarding-1mib"}
        assert recorded["large-response-forwarding-1mib"] == benchmark._LARGE_RESPONSE
        factory.assert_called_once_with(program_args=("-m", "tools.performance.memory"))
