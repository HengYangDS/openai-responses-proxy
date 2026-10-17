---
subject: openai-responses-proxy:decision-0003-provider-scoped-recovery-and-backpressure
role: decision
state: canonical
relations:
    part_of: README.md
---

# DR-0003: Isolate Recovery and Backpressure by Provider Route

- Status: accepted
- Date: 2026-08-07

## Context

Providers expose different failure envelopes. DMXAPI can return an empty-body
classification, while rate limits and retry timing belong to the provider that
issued them. A global queue, shared cooldown, or provider-name branch in core
protocol logic would couple unrelated routes and reduce usable concurrency.

## Decision

Recovery policies are selected by proved wire capability, not inferred from a
provider label. Provider-specific recovery and cooldown state is keyed to the
selected route and never blocks another route. An upstream `429` is relayed
unchanged once, then creates only a bounded provider-scoped cooldown.
After bounded request-local recovery exhausts an upstream `503`, its original
response is relayed and a short provider-scoped unavailability cooldown prevents
each subsequent client turn from multiplying the same outage. The next request
after expiry probes upstream again; other providers remain independent.

The proxy does not own a global request queue or undocumented provider quota.
Client configuration limits per-session concurrency; each provider remains the
authority for its actual service limit.

## Consequences

Adding an ordinary provider changes only the provider manifest. A genuine wire
difference may add one pure policy module and its tests without branching the
portable protocol owner. Failures on one provider do not serialize or disable
healthy providers.

## Revisit Trigger

Revisit if the product deliberately becomes a shared traffic scheduler with an
explicit, provider-neutral admission contract and independent operational
authority.
