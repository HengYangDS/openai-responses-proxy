"""Provider-portable request projection edge contracts."""

from __future__ import annotations

import json
from typing import cast

import pytest

from openai_responses_proxy.protocol.replay import content as portable_content
from openai_responses_proxy.protocol.replay import projection as rewrite
from tests.protocol.replay.fixtures import body as _body


class ProviderPortableRequestEdgeTests:
    @pytest.mark.parametrize("media_type", ["png", "jpeg", "webp", "gif"])
    @pytest.mark.parametrize("detail", ["low", "high", "auto", "original"])
    def test_preserves_inline_image_transport_bytes(self, media_type: str, detail: str) -> None:
        image = {
            "type": "input_image",
            "image_url": f"data:image/{media_type};base64,cGl4ZWxz",
            "detail": detail,
        }
        blocks = [
            {"type": "input_text", "text": "before"},
            image,
            {"type": "input_text", "text": "after"},
        ]

        projected = portable_content.project_input_content(
            blocks, allow_images=True, encrypted_marker=False
        )

        assert projected == (blocks, False, 0, 0, 0)

    @pytest.mark.parametrize("carrier", ["system", "developer", "user", "function", "custom"])
    def test_replays_inline_screenshot_without_local_image_loss(self, carrier: str) -> None:
        content = [
            {
                "type": "input_image",
                "image_url": (
                    "data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                    "AAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
                ),
                "detail": "original",
            }
        ]
        if carrier in {"function", "custom"}:
            call_type = "function_call" if carrier == "function" else "custom_tool_call"
            input_field = "arguments" if carrier == "function" else "input"
            items = [
                {"type": call_type, "call_id": "image", "name": "screenshot", input_field: "{}"},
                {"type": call_type + "_output", "call_id": "image", "output": content},
            ]
        else:
            items = [{"type": "message", "role": carrier, "content": content}]
        raw = _body({"input": items})

        projection = rewrite.sanitize_responses_body(raw)

        assert projection.body is not None, projection.diagnostic()
        assert json.loads(projection.body)["input"] == items
        assert projection.metrics.local_image_items == 0
        repeated = rewrite.sanitize_responses_body(projection.body)
        assert repeated.body == projection.body
        assert json.loads(raw)["input"] == items

    @pytest.mark.parametrize(
        "url",
        [
            "data:image/png;base64,",
            "data:image/png;base64,???",
            "data:image/png;base64,abc",
            "data:image/png;base64,cGl4ZWxz\n",
            "data:image/png;base64,é",
            "data:image/png,cGl4ZWxz",
            "data:text/html;base64,cGl4ZWxz",
            "file:///private/screenshot.png",
        ],
    )
    def test_retains_text_when_image_transport_is_not_replayable(self, url: str) -> None:
        text = {"type": "input_text", "text": "visible"}
        projected = portable_content.project_input_content(
            [{"type": "input_image", "image_url": url}, text],
            allow_images=True,
            encrypted_marker=False,
        )

        assert projected == ([text], True, 0, 0, 1)

    def test_preserves_supported_optional_portable_shapes(self) -> None:
        raw = _body(
            {
                "input": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "cannot comply"}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "https://example.test/image.png",
                                "detail": "original",
                            }
                        ],
                    },
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "lookup",
                        "arguments": "{}",
                        "caller": {"type": "direct"},
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": None,
                        "encrypted_content": "provider-bound",
                        "caller": {"type": "program", "caller_id": "planner"},
                    },
                ]
            }
        )

        _projection = rewrite.sanitize_responses_body(raw)
        projected_raw = _projection.body
        note = _projection.diagnostic()

        assert projected_raw is not None, note
        projected = json.loads(projected_raw)
        message, image_message, call, output = projected["input"]
        assert message["content"] == "cannot comply"
        assert image_message["content"][0]["detail"] == "original"
        assert call["caller"] == {"type": "direct"}
        assert output["caller"] == {"type": "program", "caller_id": "planner"}
        assert output["output"] == [
            {"type": "input_text", "text": portable_content.OPAQUE_CONTENT_MARKER}
        ]
        assert "provider-bound" not in projected_raw.decode()

    def test_preserves_agent_ciphertext_and_marks_opaque_tool_results(self, subtests) -> None:
        raw = _body(
            {
                "input": [
                    {
                        "type": "agent_message",
                        "author": "planner",
                        "recipient": "user",
                        "content": [],
                        "encrypted_content": "agent-secret",
                    },
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": [],
                        "encrypted_content": "tool-secret",
                    },
                ]
            }
        )

        _projection = rewrite.sanitize_responses_body(raw)
        projected_raw = _projection.body
        note = _projection.diagnostic()

        assert projected_raw is not None, note
        agent, _call, output = json.loads(projected_raw)["input"]
        assert agent == json.loads(raw)["input"][0]
        assert output["output"] == [
            {"type": "input_text", "text": portable_content.OPAQUE_CONTENT_MARKER}
        ]
        assert "encrypted_blocks=1" in note
        assert "omission_markers=1" in note

        for content in ("", None):
            with subtests.test(root_only_agent_content=content):
                _projection = rewrite.sanitize_responses_body(
                    _body(
                        {
                            "input": [
                                {
                                    "type": "agent_message",
                                    "author": "planner",
                                    "recipient": "user",
                                    "content": content,
                                    "encrypted_content": "agent-secret",
                                }
                            ]
                        }
                    )
                )
                projected_raw = _projection.body
                note = _projection.diagnostic()
                assert projected_raw is not None, note
                projected = json.loads(projected_raw)["input"][0]
                assert projected["type"] == "agent_message"
                assert projected["content"] == content
                assert projected["encrypted_content"] == "agent-secret"

    def test_drops_unreplayable_images_and_keeps_text_and_https(self):
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "image-call",
                        "name": "inspect",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "image-call",
                        "output": [
                            {"type": "input_text", "text": "before"},
                            {"type": "input_image", "image_url": "/tmp/example.png"},
                            {"type": "input_text", "text": "after"},
                        ],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "https://example.test/valid.png",
                            },
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,not-supported",
                            },
                        ],
                    },
                ]
            }
        ).encode()

        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))

        assert "local_image_items=2" in note
        assert obj["input"][1]["output"] == [
            {"type": "input_text", "text": "before"},
            {"type": "input_text", "text": "after"},
        ]
        assert obj["input"][2]["content"] == [
            {"type": "input_image", "image_url": "https://example.test/valid.png"}
        ]

    def test_drops_malformed_http_like_image_urls(self):
        bad_urls = [
            "https://",
            "https://bad host/example.png",
            "http:///missing-host",
            "https://example.test:not-a-port/image.png",
            "https://example.test/has space.png",
        ]
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "image-call",
                        "name": "inspect",
                        "input": "{}",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "image-call",
                        "output": [{"type": "input_image", "image_url": url} for url in bad_urls]
                        + [
                            {
                                "type": "input_image",
                                "image_url": "https://example.test/valid.png",
                            },
                        ],
                    },
                ],
            }
        ).encode()

        _projection = rewrite.sanitize_responses_body(body)
        out = _projection.body
        note = _projection.diagnostic()
        obj = json.loads(cast("bytes", out))

        assert f"local_image_items={len(bad_urls)}" in note
        assert obj["input"][1]["output"] == [
            {"type": "input_image", "image_url": "https://example.test/valid.png"}
        ]
