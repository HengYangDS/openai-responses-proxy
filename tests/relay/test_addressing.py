"""Upstream addressing: the request path names the upstream origin and resource."""

from __future__ import annotations

import pytest

from openai_responses_proxy.relay import addressing
from openai_responses_proxy.relay.addressing import Target


def test_https_target_names_its_upstream() -> None:
    assert addressing.resolve("/https/gateway.example/v1/responses") == Target(
        "https://gateway.example/v1", "responses", "https://gateway.example/v1/responses"
    )


def test_query_is_kept_on_the_url_and_never_on_the_origin() -> None:
    target = addressing.resolve("/https/api.example.com/v1/models?client_version=0.172")

    assert target == Target(
        "https://api.example.com/v1",
        "models",
        "https://api.example.com/v1/models?client_version=0.172",
    )


@pytest.mark.parametrize(
    ("path", "origin"),
    [
        ("/https/api.example.com:8443/v1/responses", "https://api.example.com:8443/v1"),
        ("/http/[::1]:9000/v1/responses", "http://[::1]:9000/v1"),
        ("/https/h.example/responses", "https://h.example"),
        ("/https/h.example/a/b/v1/responses", "https://h.example/a/b/v1"),
    ],
)
def test_authority_and_base_path_form_the_origin(path: str, origin: str) -> None:
    target = addressing.resolve(path)

    assert target is not None
    assert target.origin == origin
    assert target.url == origin + "/responses"


def test_plain_http_is_loopback_only() -> None:
    assert addressing.resolve("/http/example.com/v1/responses") is None
    assert addressing.resolve("/http/10.0.0.1/v1/responses") is None
    assert addressing.resolve("/http/127.0.0.1:9/v1/responses") is not None
    assert addressing.resolve("/http/localhost:9/v1/responses") is not None


@pytest.mark.parametrize(
    "path",
    [
        "/https/h/v1//responses",
        "/https/h/./responses",
        "/https/h/../responses",
        "/https//v1/responses",
    ],
)
def test_empty_and_dot_segments_are_refused(path: str) -> None:
    assert addressing.resolve(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "/ftp/h/v1/responses",
        "/HTTPS/h/v1/responses",
        "/https/u@h/v1/responses",
        "/https/u:p@h/v1/responses",
        "/https/h/v1/messages",
        "/https/h/v1/chat/completions",
        "/https/h/v1/responses/extra",
        "/https/h/v1/Responses",
        "https://h/v1/responses",
        "//h/v1/responses",
        "/https/h/v1/responses#fragment",
        "/https/h/v%31/responses",
        "/https/h%2Fx/v1/responses",
        "/https/h:notaport/v1/responses",
        "/https/h:99999/v1/responses",
        "/https;agent=plaintext/h/v1/responses",
        "/https/h/v1;x=1/responses",
        "/https/responses",
        "/responses",
        "",
    ],
)
def test_malformed_targets_are_refused(path: str) -> None:
    assert addressing.resolve(path) is None
