"""Frozen relay recovery preserves current-turn semantics and deadline bounds."""

from __future__ import annotations

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
def test_frozen_input_recovery_preserves_required_current_turn(required, tmp_path: Path) -> None:
    """Exercise exact input-validation recovery through a frozen loopback listener."""
    upstream = ScriptedUpstream()
    port = free_port()
    with ExitStack() as cleanup:
        root = Path(cleanup.enter_context(tempfile.TemporaryDirectory(dir=tmp_path)))
        ctx = write_installed_payload(
            root, release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        cleanup.callback(terminate_process, child)
        cleanup.callback(upstream.close)
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
        assert http_json(port, "/healthz")[1]["active_responses"] == 0

    assert child.poll() is not None
    assert not proxy_is_up(port)
    assert not proxy_is_up(upstream.port)
    assert not upstream.thread.is_alive()
    assert not root.exists()


def test_frozen_reconnect_header_acquisition_obeys_stream_deadline(tmp_path: Path) -> None:
    """Delayed reconnect headers cannot extend the native stream budget."""
    upstream = ScriptedUpstream()
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

    upstream.push(first)
    upstream.push(second)
    port = free_port()
    with ExitStack() as cleanup:
        root = Path(cleanup.enter_context(tempfile.TemporaryDirectory(dir=tmp_path)))
        ctx = write_installed_payload(
            root, release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        ctx.upstream_timeout = 1.1
        ctx.upstream_read_timeout = 0.1
        runtime_spec.write(ctx)
        child = start_real_proxy(
            ctx,
            upstream_url=upstream.base_url(),
            log_path=root / "proxy.log",
        )
        cleanup.callback(terminate_process, child)
        cleanup.callback(upstream.close)
        cleanup.callback(release.set)
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
            assert error.code == 503
            payload = json.loads(error.read())
        assert payload["error"]["code"] == "stream_pre_content_exhausted"
        assert payload["error"]["attempts"] == 2
        assert len(upstream.received) == 2
        assert started.is_set()
        assert not release.is_set()
        assert elapsed < 1.8
        assert http_json(port, "/healthz")[1]["active_responses"] == 0

    assert child.poll() is not None
    assert not proxy_is_up(port)
    assert not proxy_is_up(upstream.port)
    assert not upstream.thread.is_alive()
    assert not root.exists()
