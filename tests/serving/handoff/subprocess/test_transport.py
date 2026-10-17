"""Real subprocess replay and pre-content stream recovery."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tests.serving.handoff.fixtures import start_real_proxy
from tests.serving.handoff.fixtures import terminate_process
from tests.serving.handoff.subprocess.fixtures import SubprocessFixture

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestRealSubprocessHandoffIntegration(SubprocessFixture):
    @pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
    def test_native_listener_preserves_tool_deliveries_and_encrypted_agent_tasks(self, call_type):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        completed = b'data: {"type":"response.completed"}\n\n'

        def send(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(completed)))
            handler.end_headers()
            handler.wfile.write(completed)

        upstream.push(send)
        port = free_port()
        root, ctx, _owned = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(child))
        upstream.start()
        argument = "arguments" if call_type == "function_call" else "input"
        call = {"type": call_type, "call_id": "job", "name": "run", argument: "{}"}
        first = {"type": f"{call_type}_output", "call_id": "job", "output": "Running"}
        latest = {"type": "message", "role": "user", "content": "Continue"}
        delivery = {**first, "id": "delivery", "name": "run", "output": "Complete"}
        task = {
            "type": "agent_message",
            "author": "/root",
            "recipient": "/root/reviewer",
            "content": [
                {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                {"type": "encrypted_content", "encrypted_content": "fixture-task"},
            ],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=json.dumps(
                {
                    "model": "synthetic",
                    "stream": True,
                    "input": [call, first, latest, delivery, task],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            assert response.read() == completed
        assert len(upstream.received) == 1
        items = json.loads(upstream.received[0])["input"]
        assert items[:3] == [call, first, latest]
        assert items[3] == {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": '{"type":"tool_delivery","call_id":"job","name":"run"}\nComplete',
        }
        assert items[4] == task

    @pytest.mark.parametrize("ending", ["closed", "held_open", "malformed"])
    def test_native_listener_recovers_pre_content_failure_without_duplicate_output(self, ending):
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)
        release = threading.Event()
        self._cleanups.callback(release.set)
        request_id = "64781e82-1e8f-42af-a06a-409e154bbfe2"
        failed = (
            b'data: {"type":"response.created"}\n\n'
            b'data: {"type":"response.failed","response":{"error":{"code":"server_error",'
            b'"message":"request ID ' + request_id.encode() + b'"}}}\n\ndata: [DONE]\n\n'
        )
        completed = (
            b'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
            b'data: {"type":"response.completed"}\n\n'
        )
        if ending == "malformed":
            completed = completed.split(b'data: {"type":"response.completed"}')[0]
            completed += b'data: {"type":\n\n'

        def send(handler):
            payload = failed if len(upstream.received) == 1 else completed
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            held = ending == "held_open" and len(upstream.received) == 2
            if held:
                handler.send_header("Transfer-Encoding", "chunked")
            else:
                handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload) if held else payload)
            handler.wfile.flush()
            if held:
                release.wait(timeout=10)
                handler.close_connection = True

        upstream.push(send)
        upstream.push(send)
        port = free_port()
        root, ctx, _owned = self._installed_fixture(
            release="1.0.25", port=port, upstream_url=upstream.base_url()
        )
        child = start_real_proxy(ctx, upstream_url=upstream.base_url(), log_path=root / "proxy.log")
        self._cleanups.callback(lambda: terminate_process(child))
        upstream.start()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=b'{"model":"synthetic","stream":true,"input":[]}',
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                if ending == "malformed":
                    with pytest.raises(http.client.IncompleteRead) as raised:
                        response.read()
                    assert b'"delta":"recovered"' in raised.value.partial
                    assert b"stream_projection_failed" not in raised.value.partial
                else:
                    assert response.read() == completed
                assert not release.is_set()
        finally:
            release.set()
        assert len(upstream.received) == 2
        assert upstream.received[0] == upstream.received[1]
        logs = (Path(ctx.log_dir) / "proxy.log").read_text(encoding="utf-8")
        assert f"upstream_request_id={request_id}" in logs
        failure = next(line for line in logs.splitlines() if "event=sse_upstream_failed" in line)
        assert "code=server_error" in failure
        assert "committed=False" in failure

    def test_scripted_upstream_starts_only_after_the_proxy_child_is_spawned(
        self,
    ) -> None:
        upstream = ScriptedUpstream()
        self._cleanups.callback(upstream.close)

        assert not upstream.thread.is_alive()
        upstream.start()
        assert upstream.thread.is_alive()
