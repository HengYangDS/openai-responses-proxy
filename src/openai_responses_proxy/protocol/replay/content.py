"""Provider-portable message content projection."""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from typing import Never
from typing import cast

type JsonObject = dict[str, object]

OPAQUE_CONTENT_MARKER = "[opaque provider content omitted: not portable across providers]"


class ProjectionRejectedError(ValueError):
    """A replay structure has no proved provider-portable representation."""


def reject(reason: str) -> Never:
    """Reject one structure using its bounded diagnostic code."""
    raise ProjectionRejectedError(reason)


def _is_replayable_image_url(value: object) -> bool:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        return False
    if value[:5].lower() == "data:":
        header, separator, payload = value.partition(",")
        if not separator or header.lower() not in {
            "data:image/png;base64",
            "data:image/jpeg;base64",
            "data:image/webp;base64",
            "data:image/gif;base64",
        }:
            return False
        try:
            return bool(base64.b64decode(payload, validate=True))
        except (binascii.Error, ValueError):
            return False
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.hostname is None:
            return False
        _ = parsed.port
    except ValueError:
        return False
    return True


def _text_block_value(typed: JsonObject, block_type: object) -> str:
    if block_type == "input_text":
        allowed = {"type", "text", "prompt_cache_breakpoint"}
        if set(typed) - allowed:
            reject("invalid_text_block")
        cache_breakpoint = typed.get("prompt_cache_breakpoint")
        if cache_breakpoint is not None and cache_breakpoint != {"mode": "explicit"}:
            reject("invalid_text_block")
    else:
        allowed = {"type", "text", "annotations", "logprobs"}
        if set(typed) - allowed:
            reject("invalid_text_block")
        if "annotations" in typed and not isinstance(typed["annotations"], list):
            reject("invalid_text_block")
        if (
            "logprobs" in typed
            and typed["logprobs"] is not None
            and not isinstance(typed["logprobs"], list)
        ):
            reject("invalid_text_block")
    text = typed.get("text")
    if not isinstance(text, str):
        reject("invalid_text_block")
    return text


def project_input_content(
    value: object,
    *,
    allow_images: bool,
    encrypted_marker: bool,
    root_ciphertext: int = 0,
) -> tuple[object, bool, int, int, int]:
    """Return provider-neutral input content and bounded projection metrics."""
    if isinstance(value, str) and value:
        return value, bool(root_ciphertext), root_ciphertext, 0, 0
    if value is None or isinstance(value, str):
        if root_ciphertext and encrypted_marker:
            return (
                [{"type": "input_text", "text": OPAQUE_CONTENT_MARKER}],
                True,
                root_ciphertext,
                1,
                0,
            )
        reject("empty_text_content" if isinstance(value, str) else "invalid_content")
    if not isinstance(value, list):
        reject("invalid_content")
    blocks = cast("list[object]", value)

    projected: list[JsonObject] = []
    changed = False
    encrypted = root_ciphertext
    local_images = 0
    for block in blocks:
        if not isinstance(block, dict):
            reject("invalid_content_block")
        typed = cast(JsonObject, block)
        block_type = typed.get("type")
        if block_type in ("input_text", "output_text"):
            text = _text_block_value(typed, block_type)
            projected.append({"type": "input_text", "text": text})
            changed = changed or set(typed) != {"type", "text"} or block_type != "input_text"
        elif block_type == "refusal":
            if set(typed) != {"type", "refusal"} or not isinstance(typed.get("refusal"), str):
                reject("invalid_refusal_block")
            reject("invalid_refusal_role")
        elif block_type == "encrypted_content":
            encrypted += 1
            changed = True
        elif block_type == "input_image" and allow_images:
            allowed = {"type", "image_url", "detail"}
            if set(typed) - allowed:
                reject("invalid_image_block")
            if not _is_replayable_image_url(typed.get("image_url")):
                local_images += 1
                changed = True
                continue
            image = {"type": "input_image", "image_url": typed["image_url"]}
            detail = typed.get("detail")
            if detail is not None:
                if detail not in ("low", "high", "auto", "original"):
                    reject("invalid_image_detail")
                image["detail"] = detail
            projected.append(image)
        else:
            reject("unknown_content_type")

    markers = 0
    only_empty_text = projected and all(
        block.get("type") == "input_text" and block.get("text") == "" for block in projected
    )
    if encrypted and encrypted_marker and (not projected or only_empty_text):
        projected.clear()
        projected.append({"type": "input_text", "text": OPAQUE_CONTENT_MARKER})
        markers = 1
        changed = True
    if not projected:
        reject("empty_portable_content")
    return projected, changed, encrypted, markers, local_images


def project_assistant_text(
    value: object,
    *,
    encrypted_marker: bool,
    root_ciphertext: int = 0,
) -> tuple[str, bool, int, int]:
    """Project assistant history to the provider-neutral easy-message string."""
    if isinstance(value, str):
        if value:
            return value, False, root_ciphertext, 0
        if root_ciphertext and encrypted_marker:
            return OPAQUE_CONTENT_MARKER, True, root_ciphertext, 1
        reject("empty_text_content")
    if not isinstance(value, list):
        if root_ciphertext and value is None and encrypted_marker:
            return OPAQUE_CONTENT_MARKER, True, root_ciphertext, 1
        reject("invalid_content")

    text_parts: list[str] = []
    encrypted = root_ciphertext
    changed = bool(root_ciphertext)
    for block in cast("list[object]", value):
        if not isinstance(block, dict):
            reject("invalid_content_block")
        typed = cast(JsonObject, block)
        block_type = typed.get("type")
        if block_type in ("input_text", "output_text"):
            text_parts.append(_text_block_value(typed, block_type))
            changed = True
        elif block_type == "refusal":
            refusal = typed.get("refusal")
            if set(typed) != {"type", "refusal"} or not isinstance(refusal, str):
                reject("invalid_refusal_block")
            text_parts.append(refusal)
            changed = True
        elif block_type == "encrypted_content":
            encrypted += 1
            changed = True
        elif block_type == "input_image":
            reject("unsupported_assistant_image")
        else:
            reject("unknown_content_type")

    markers = 0
    text = "".join(text_parts)
    if not text and encrypted and encrypted_marker:
        text = OPAQUE_CONTENT_MARKER
        markers = 1
    if not text:
        reject("empty_portable_content")
    return text, changed, encrypted, markers


def empty_assistant_placeholder(value: object) -> bool:
    """Return whether content is one valid, empty assistant placeholder."""
    if not isinstance(value, list) or len(value) != 1:
        return False
    block = value[0]
    if not isinstance(block, dict):
        return False
    typed = cast(JsonObject, block)
    if typed.get("type") != "output_text":
        return False
    try:
        return _text_block_value(typed, "output_text") == ""
    except ProjectionRejectedError:
        return False
