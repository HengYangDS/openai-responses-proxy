"""Canonical aggregate identity for an explicit serving-file digest map."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TypeGuard

from openai_responses_proxy.json_value import FrozenJsonObject
from openai_responses_proxy.json_value import freeze_object
from openai_responses_proxy.json_value import is_json_object

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PayloadDigestError(ValueError):
    """Report an invalid path-to-SHA-256 aggregate input."""


def canonical_json(value: object) -> bytes:
    """Encode one mapping in the canonical compact JSON form used by release state."""
    if not is_json_object(value):
        raise PayloadDigestError("canonical JSON requires a finite object with string keys")
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def freeze_mapping(value: object) -> FrozenJsonObject:
    """Recursively freeze one JSON-like mapping for immutable release evidence."""
    return freeze_object(value)


def aggregate_file_digests_sha256(file_digests: Mapping[str, str]) -> str:
    """Hash sorted length-delimited UTF-8 paths and raw SHA-256 digest bytes."""
    aggregate = hashlib.sha256()
    for relative in sorted(file_digests):
        digest = file_digests[relative]
        if not isinstance(relative, str) or not relative or _SHA256.fullmatch(digest) is None:
            raise PayloadDigestError(f"invalid serving payload digest: {relative}")
        path_bytes = relative.encode("utf-8")
        digest_bytes = bytes.fromhex(digest)
        aggregate.update(len(path_bytes).to_bytes(8, "big"))
        aggregate.update(path_bytes)
        aggregate.update(len(digest_bytes).to_bytes(8, "big"))
        aggregate.update(digest_bytes)
    return aggregate.hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one regular file."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def is_sha256(value: object) -> TypeGuard[str]:
    """Return whether a value is one lowercase SHA-256 digest."""
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
