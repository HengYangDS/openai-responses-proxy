## Why

The independent review of 8.0.0 left seven minor findings. On 2026-11-28 the owner chose to fix six of them (M1 and
M3 to M7) and asked that each part reach its intended end state, not a stopgap. For M3 he gave the reason: the proxy
will not serve only today's four upstreams. Three items from earlier retrospectives are also still open: relayed
responses carry two `Server` and two `Date` fields; a failing governance check does not stop `mise run check`, which
on 2026-11-26 ran 12 minutes after Prettier had already refused; and the rule that came out of 2026-11-27's
predecessor-fixture failure has not been written down.

## What Changes

- Every upstream origin has one canonical spelling. The proxy folds case, writes the port in decimal, drops the
  scheme's default port and writes an IPv6 literal in its standard form. It refuses host forms that a resolver
  reads differently from how they are written, and host names that DNS cannot carry. Cooldowns, pooled
  connections, log lines and the forwarded URL use that spelling. This also answers M2: the host grammar bounds
  the host, and the listener's request-line limit already bounds the base path.
- Idle upstream connections stay bounded however many origins clients name. The pool holds at most 32 in total
  and still at most 8 per origin. The listener's own serve loop closes an idle connection once it is 30 seconds
  old, whether or not another request arrives.
- A data request from a client that is not local gets a structured 403 error with the code
  `local_client_required`, and a log line. Before, it got the standard library's HTML page and no log line.
- Each refusal goes by one name: the error code the client receives. The route counters, failure classes and log
  events now say `upstream_route_invalid` and `upstream_route_method_not_allowed` instead of
  `provider_route_rejected` and `provider_route_method_rejected`.
- An upstream error, stream or model catalog carries the upstream's `Server` and `Date` once, and the proxy adds
  `Date` only when the upstream sent none. Responses that the proxy writes itself, a validated non-stream body
  included, keep the proxy's own fields.
- `mise run check` stops at its first failing session, so governance, which takes seconds, fails before the slow
  sessions start.
- CONTRIBUTING requires a change to the payload's shape to list every reader of another release's archive,
  test fixtures included, before the proof.
- A docstring that still says "provider manifest" is corrected (M6). The agent-delivery test now compares the
  relayed body with an expected body written out in the test, instead of one computed by the code under test (M7).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-portable-responses`: canonical upstream origins, the bound on idle connections across origins, the
  structured local-client refusal and its names, and the single `Server` and `Date` of a relayed response.

## Impact

- Product code under `relay/` and `serving/`; release 8.0.1 (fixes only).
- `status --json` counters: two route counters are renamed. The individual counter names are not a documented
  contract (README lists "bounded reliability counters"), and CHANGELOG names the rename.
- No change for AIGW: it already writes lower-case hosts without default ports.
