## Why

Measurements on the morning of 2026-11-27 suggested that DMXAPI's `gpt-6.2-sol-cdx` reasons only on a request that
is exactly what a Codex conversation held on DMXAPI sends, and that the proxy's portable projection therefore turned
its reasoning off. The owner chose to forward stateless requests unchanged on the DMXAPI route, released as 7.0.1.
The acceptance after installation did not restore reasoning (31 tokens in the first turn, 0 in the second), and an
alternating measurement the same day gave no reasoning on any path, DMXAPI's own endpoint without the proxy included.
The route had no measured benefit, and the morning's comparison had measured shapes at different times. The owner
withdrew the route, released as 7.0.2.

## What Changes

- 7.0.1 (released 2026-11-27): a route could declare in the provider manifest that it forwards stateless requests
  unchanged, and DMXAPI did. The first attempt of a request with `store=false` and no `previous_response_id`,
  `conversation` or item reference reached the upstream byte for byte; every later attempt sent the projected
  request, and an HTTP 400 to the client's bytes was answered by one projected attempt.
- 7.0.2 withdraws all of it. The code, tests, README and architecture document return to their state before 7.0.1,
  DR-0002 records the amendment and its withdrawal, and every route again builds every attempt from the projected
  request.
- The specification delta is dropped: the main specifications never held the pass-through requirements, so the
  Change archives without spec updates (`skip_specs`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `provider-portable-responses` is unchanged once 7.0.2 has withdrawn 7.0.1's delta.

## Impact

Released versions 7.0.1 and 7.0.2. Net source effect against a5e682e3, the source just before 7.0.1:
`docs/decisions/dr-0002-provider-portable-stateless-replay.md` (the withdrawal paragraph), `CHANGELOG.md` and
`VERSION`. At the time of the measurement, Codex conversations on DMXAPI did not reason through the proxy or without
it. Out of scope: the issuer-boundary redesign (8.0.0) and any measurement of UCloud or AIHubMix.
