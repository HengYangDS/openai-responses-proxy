---
subject: openai-responses-proxy:decision-0003-provider-scoped-recovery-and-backpressure
role: decision
decision_id: DR-0003
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0003: Isolate Recovery and Backpressure by Provider Route

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

## Alternatives Rejected

- **Global queue or cooldown:** rejected because an outage on one route would
  delay healthy providers with unrelated quotas.
- **Provider-name branches in portable protocol logic:** rejected because
  routing labels do not establish wire capability or recovery semantics.
- **Unbounded retries:** rejected because they amplify outages and make a
  client turn's cost and termination unpredictable.

## Consequences and Boundary

Adding an ordinary provider changes only the provider manifest. A genuine wire
difference may add one pure policy module and its tests without branching the
portable protocol owner. Failures on one provider do not serialize or disable
healthy providers.

## Evidence and Revisit

The [provider-portable Responses specification](../../openspec/specs/provider-portable-responses/spec.md)
owns request-local recovery and provider-scoped cooldown requirements.

Revisit if the product deliberately becomes a shared traffic scheduler with an
explicit, provider-neutral admission contract and independent operational
authority.
