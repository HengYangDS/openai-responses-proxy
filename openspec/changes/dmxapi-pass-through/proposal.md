## Why

DMXAPI's `gpt-6.2-sol-cdx` reasons only on a request that is exactly what a Codex conversation held on DMXAPI alone
sends. The proxy projects every request to a portable form, and any change to the body (removing only the message
items' `id`, or only `prompt_cache_key` and the `include` entry) makes DMXAPI answer with 0 reasoning tokens and no
error. Codex conversations sent to DMXAPI through the proxy have therefore not reasoned. The measurements were made
on 2026-11-27 through a throwaway build of the proxy; the issuer-boundary design records them, and the owner chose
this fix on 2026-11-27.

## What Changes

- A route may declare in the provider manifest that it forwards stateless requests unchanged. A request is stateless
  when the client already sends `store=false` and no `previous_response_id`, `conversation` or item reference, as
  Codex does. The first attempt of such a request reaches the upstream byte for byte, including reasoning items,
  identifiers, `prompt_cache_key` and `include`. Any other request takes the portable projection as before.
- The portable grammar still checks every request first: a request it rejects is rejected locally on every route.
- The client's bytes are sent at most once. Every later attempt, after any HTTP 400, a transient failure, DMXAPI's
  477 or a stream failure before content, sends the projected request, so a failure takes 7.0.0's path one round
  trip later.
- DMXAPI declares the pass-through shape. UCloud, AIHubMix and Suhe.AI stay portable.
- The manifest refuses pass-through together with plaintext agent delivery, which must rewrite the body.
- Release 7.0.1.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-portable-responses`: every attempt sends the projected request except the first attempt of a stateless
  request on a route that declares pass-through; requirements on the outbound form name the projected request.

## Impact

`src/openai_responses_proxy/providers/` (manifest and registry), `protocol/replay/projection.py`, `relay/responses.py`,
`relay/exchange.py` and `relay/telemetry.py`, their tests, `docs/decisions/dr-0002-provider-portable-stateless-replay.md`
(amendment), `docs/architecture/authority-and-runtime-boundary.md`, README, CHANGELOG and `VERSION`. A conversation that has switched providers still does not reason on DMXAPI: DMXAPI accepts
another issuer's reasoning item but does not reason. UCloud and AIHubMix items in a body sent to DMXAPI are not
measured; any HTTP 400 for them is answered by the projected request. Out of scope: the issuer-boundary redesign
(8.0.0).
