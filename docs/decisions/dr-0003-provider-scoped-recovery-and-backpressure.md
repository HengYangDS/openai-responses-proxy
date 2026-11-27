---
subject: openai-responses-proxy:decision-0003-provider-scoped-recovery-and-backpressure
role: decision
decision_id: DR-0003
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0003: Isolate Recovery and Backpressure by Upstream Origin

- Date: 2026-08-07
- Last amended: 2026-11-27

## Context

Providers expose different failure envelopes. DMXAPI can return an empty-body
classification, while rate limits and retry timing belong to the provider that
issued them. A global queue, shared cooldown, or provider-name branch in core
protocol logic would couple unrelated routes and reduce usable concurrency.

## Decision

Recovery is selected by proved wire capability, keyed by the response it
answers, and never inferred from an upstream's name. Cooldown state is keyed by
the upstream origin, the scheme, authority and base path that the request path
names, and never blocks another origin. An upstream `429` is relayed unchanged
once, then creates only a bounded cooldown for that origin. After bounded
request-local recovery exhausts an upstream `503`, its original response is
relayed and a short unavailability cooldown for that origin prevents each
subsequent client turn from multiplying the same outage. The next request after
expiry probes upstream again; other origins remain independent.

The 2026-11-27 amendment (release 8.0.0) replaced the provider route with the
upstream origin as the key, and removed DMXAPI's empty-response retry: no HTTP
477 appeared among 21,414 requests in eight days, so a 477 is now relayed like
any other client error.

The proxy does not own a global request queue or undocumented provider quota.
Client configuration limits per-session concurrency; each provider remains the
authority for its actual service limit.

## Alternatives Rejected

- **Global queue or cooldown:** rejected because an outage on one route would
  delay healthy providers with unrelated quotas.
- **Provider-name branches in portable protocol logic:** rejected because
  routing labels do not establish wire capability or recovery semantics.
- **Unbounded retries:** rejected because they amplify outages and make a
  client turn's cost and termination unpredictable.

## Consequences and Boundary

Adding an upstream needs no release: the client names it. A genuine wire
difference may add one recovery keyed by the response, with its tests, without
branching the portable protocol owner. Failures at one origin do not serialize
or disable healthy origins.

## Evidence and Revisit

The [provider-portable Responses specification](../../openspec/specs/provider-portable-responses/spec.md)
owns request-local recovery and per-origin cooldown requirements.

Revisit if the product deliberately becomes a shared traffic scheduler with an
explicit, provider-neutral admission contract and independent operational
authority.
