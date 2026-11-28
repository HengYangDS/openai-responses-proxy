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


@pytest.mark.parametrize(
    ("path", "origin"),
    [
        ("/https/API.Example.COM/v1/responses", "https://api.example.com/v1"),
        ("/https/api.example.com:443/v1/responses", "https://api.example.com/v1"),
        ("/https/api.example.com:0443/v1/responses", "https://api.example.com/v1"),
        ("/https/api.example.com:08443/v1/responses", "https://api.example.com:8443/v1"),
        ("/http/LOCALHOST:80/v1/responses", "http://localhost/v1"),
        ("/http/127.0.0.1:0080/v1/responses", "http://127.0.0.1/v1"),
        ("/https/[0:0:0:0:0:0:0:1]/v1/responses", "https://[::1]/v1"),
        ("/https/[2001:DB8::0:1]:443/v1/responses", "https://[2001:db8::1]/v1"),
        ("/https/my_host.example/v1/responses", "https://my_host.example/v1"),
        ("/https/api.example.com:/v1/responses", "https://api.example.com/v1"),
        ("/http/[::1]:/v1/responses", "http://[::1]/v1"),
    ],
)
def test_each_upstream_has_one_canonical_origin(path: str, origin: str) -> None:
    """Spellings that RFC 3986 makes equivalent resolve to one origin, the URL that is sent."""
    target = addressing.resolve(path)

    assert target is not None
    assert target.origin == origin
    assert target.url == origin + "/responses"


def test_the_loopback_rule_reads_the_canonical_host() -> None:
    assert addressing.resolve("/http/LocalHost:9/v1/responses") is not None
    assert addressing.resolve("/http/[0:0::1]:9/v1/responses") is not None


@pytest.mark.parametrize(
    "path",
    [
        "/https/h.example:0/v1/responses",
        "/https/h.example:00/v1/responses",
        "/https/[fe80::1%25en0]/v1/responses",
        "/https/[v1.future]/v1/responses",
        "/https/[::1/v1/responses",
        "/https/127.1/v1/responses",
        "/https/0x7f.0.0.1/v1/responses",
        "/https/0177.0.0.1/v1/responses",
        "/https/127.0.0.01/v1/responses",
        "/https/2130706433/v1/responses",
        "/https/256.0.0.1/v1/responses",
        "/https/api.example.123/v1/responses",
        "/https/api.0x1/v1/responses",
        "/https/h..example/v1/responses",
        "/https/h.example./v1/responses",
        "/https/.h.example/v1/responses",
        f"/https/{'a' * 64}.example/v1/responses",
        f"/https/{'.'.join(['a' * 63] * 4)}/v1/responses",
    ],
)
def test_hosts_that_resolvers_or_dns_read_otherwise_are_refused(path: str) -> None:
    assert addressing.resolve(path) is None


def test_the_longest_dns_name_is_admitted() -> None:
    host = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])

    assert len(host) == 253
    target = addressing.resolve(f"/https/{host}/v1/responses")
    assert target is not None
    assert target.origin == f"https://{host}/v1"
