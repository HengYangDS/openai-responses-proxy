"""Frozen relay recovery preserves current-turn semantics and deadline bounds."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import urllib.error
from contextlib import ExitStack
from pathlib import Path

import pytest

from openai_responses_proxy.lifecycle import runtime_spec
from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tests.serving.handoff.fixtures import http_json
from tests.serving.handoff.fixtures import proxy_is_up
from tests.serving.handoff.fixtures import start_real_proxy
from tests.serving.handoff.fixtures import terminate_process
from tests.serving.handoff.fixtures import wait_until
from tests.serving.handoff.fixtures import write_installed_payload

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


@pytest.mark.parametrize(
    "required",
    [
        pytest.param(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Explain this current image"},
                    {
                        "type": "input_image",
                        "image_url": "https://example.invalid/current.png",
                        "detail": "high",
                    },
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
def test_frozen_input_recovery_preserves_required_current_turn(required) -> None:
    """Exercise exact input-validation recovery through a frozen loopback listener."""
    port = free_port()
    with ExitStack() as cleanup:
        # Frozen DLL loading needs a native temporary root, not pytest's nested
        # case path; the other native handoff journeys use this same boundary.
        root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
        upstream = ScriptedUpstream()
        cleanup.callback(upstream.close)
        ctx = write_installed_payload(
            root, release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        cleanup.callback(terminate_process, child)
        upstream.start()
        error = {
            "error": {
                "message": "invalid request body: Invalid 'input': value did not match any expected variant",
                "type": "invalid_request_error",
                "param": "",
                "code": "validation_error",
            }
        }
        upstream.push((400, json.dumps(error).encode()))
        upstream.push((200, b'{"id":"recovered","status":"completed"}'))
        current = {"type": "message", "role": "user", "content": "current request"}
        payload = {
            "input": [
                {"type": "message", "role": "user", "content": "old" * 1000},
                current,
                required,
            ]
        }

        status, response = http_json(
            port, "/dmxapi/v1/responses", method="POST", body=payload, timeout=5
        )

        assert status == 200
        assert response["status"] == "completed"
        assert len(upstream.received) == 2
        initial = json.loads(upstream.received[0])["input"]
        recovered = json.loads(upstream.received[1])["input"]
        assert initial == payload["input"]
        assert recovered == ([required] if required["type"] == "message" else [current, required])
        assert len(upstream.received[1]) < len(upstream.received[0])
        assert wait_until(
            lambda: http_json(port, "/healthz", timeout=0.5)[1]["active_responses"] == 0,
            timeout=2,
        )

    assert child.poll() is not None
    assert not proxy_is_up(port)
    assert not proxy_is_up(upstream.port)
    assert not upstream.thread.is_alive()
    assert not root.exists()


@pytest.mark.parametrize(
    "phase",
    [
        "headers",
        "json",
        "error",
        "committed-stream",
        "error-framing",
        "error-oversized",
        "slow-framing",
    ],
)
def test_frozen_exchange_deadline_covers_headers_and_body_without_replay(phase: str) -> None:
    """One native request budget bounds stalled headers and both response body kinds."""
    release = threading.Event()
    content = b'data: {"type":"response.output_text.delta","delta":"keep"}\n\n'

    def delayed(handler):
        if phase == "slow-framing":
            handler.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Progress: ")
            for _ in range(20):
                if release.wait(timeout=0.08):
                    return
                handler.connection.sendall(b"x")
            handler.connection.sendall(b"\r\nContent-Length: 2\r\n\r\n{}")
            return
        if phase in {"headers", "json", "error", "committed-stream"}:
            release.wait(timeout=5 if phase == "headers" else 0.2)
        handler.send_response(400 if phase.startswith("error") else 200)
        handler.send_header(
            "Content-Type",
            "text/event-stream" if phase == "committed-stream" else "application/json",
        )
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        if phase == "error-framing":
            handler.wfile.write(b"not-a-chunk\r\n")
            handler.wfile.flush()
            return
        if phase == "error-oversized":
            payload = b"x" * (8 * 1024 * 1024 + 1)
            handler.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
            handler.wfile.flush()
            return
        prefix = content if phase == "committed-stream" else b'{"id":"partial",'
        handler.wfile.write(b"%X\r\n%s\r\n" % (len(prefix), prefix))
        handler.wfile.flush()
        release.wait(timeout=5)
        handler.wfile.write(b"0\r\n\r\n")

    port = free_port()
    with ExitStack() as cleanup:
        root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
        upstream = ScriptedUpstream()
        cleanup.callback(upstream.close)
        cleanup.callback(release.set)
        upstream.push(delayed)
        ctx = write_installed_payload(
            root, release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        ctx.upstream_timeout = 0.5
        ctx.upstream_read_timeout = 2.0
        runtime_spec.write(ctx)
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        cleanup.callback(terminate_process, child)
        upstream.start()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        cleanup.callback(connection.close)
        begin = time.monotonic()
        connection.request(
            "POST", "/dmxapi/v1/responses", json.dumps({"input": "current", "stream": True})
        )

        with connection.getresponse() as response:
            if phase == "committed-stream":
                assert response.status == 200
                with pytest.raises(http.client.IncompleteRead) as raised:
                    response.read()
                assert raised.value.partial == content
            elif phase in {"error-framing", "error-oversized"}:
                assert response.status == 502
                error = json.loads(response.read())["error"]
                assert error["code"] == "upstream_error_body_unreadable"
                assert error["reason"] == (
                    "IncompleteRead" if phase == "error-framing" else "response_too_large"
                )
            else:
                assert response.status == 504
                payload = json.loads(response.read())
                assert payload["error"]["code"] == "upstream_deadline_exceeded"

        assert time.monotonic() - begin < 1.2
        assert len(upstream.received) == 1
        assert not release.is_set()
        assert wait_until(
            lambda: http_json(port, "/healthz", timeout=0.5)[1]["active_responses"] == 0,
            timeout=2,
        )

    assert child.poll() is not None
    assert not proxy_is_up(port)
    assert not proxy_is_up(upstream.port)
    assert not upstream.thread.is_alive()
    assert not root.exists()


def test_frozen_reconnect_header_acquisition_obeys_stream_deadline() -> None:
    """Delayed reconnect headers cannot extend the native stream budget."""
    release = threading.Event()
    started = threading.Event()

    def first(handler):
        failed = b'data: {"type":"response.failed","response":{"output":[],"error":{"code":"server_error"}}}\n\n'
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(failed)))
        handler.end_headers()
        handler.wfile.write(failed)

    def second(handler):
        started.set()
        release.wait(timeout=5)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    port = free_port()
    with ExitStack() as cleanup:
        root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
        upstream = ScriptedUpstream()
        cleanup.callback(upstream.close)
        cleanup.callback(release.set)
        upstream.push(first)
        upstream.push(second)
        ctx = write_installed_payload(
            root, release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        # Leave a full second for real reconnect scheduling after native backoff.
        ctx.upstream_timeout = 2.0
        ctx.upstream_read_timeout = 0.1
        runtime_spec.write(ctx)
        child = start_real_proxy(
            ctx,
            upstream_url=upstream.base_url(),
            log_path=root / "proxy.log",
        )
        cleanup.callback(terminate_process, child)
        upstream.start()
        begin = time.monotonic()

        with pytest.raises(urllib.error.HTTPError) as raised:
            http_json(
                port,
                "/dmxapi/v1/responses",
                method="POST",
                body={"input": "current", "stream": True},
                timeout=5,
            )
        elapsed = time.monotonic() - begin
        with raised.value as error:
            assert error.code == 504
            payload = json.loads(error.read())
        assert payload["error"]["code"] == "upstream_deadline_exceeded"
        assert len(upstream.received) == 2
        assert started.is_set()
        assert not release.is_set()
        assert elapsed < 3
        # Downstream delivery precedes the request's finally block. Observe
        # terminal admission release, not an unrelated health request's race.
        assert wait_until(
            lambda: http_json(port, "/healthz", timeout=0.5)[1]["active_responses"] == 0,
            timeout=2,
        )

    assert child.poll() is not None
    assert not proxy_is_up(port)
    assert not proxy_is_up(upstream.port)
    assert not upstream.thread.is_alive()
    assert not root.exists()
