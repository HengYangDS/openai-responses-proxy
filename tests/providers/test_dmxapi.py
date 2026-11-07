"""DMXAPI wire-policy extension contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai_responses_proxy.providers.policies import dmxapi

if TYPE_CHECKING:
    import pytest


class DmxapiPolicyContracts:
    def test_dmxapi_policy_owns_the_http_477_empty_response_extension(
        self, subtests: pytest.Subtests
    ) -> None:
        exact = b'{"error":{"type":"dmx_api_error","code":"empty_response"}}'
        assert dmxapi.is_retryable_failure(477, exact)
        for rejected in (b'{"error":"unprocessable"}', b"not-json", b"[]"):
            with subtests.test(rejected=rejected):
                assert not dmxapi.is_retryable_failure(477, rejected)
        assert not dmxapi.is_retryable_failure(400, exact)
