## ADDED Requirements

### Requirement: The proxy owns no ordinary concurrency limit

The proxy SHALL NOT serialize a provider route, queue ordinary traffic, or own
a fixed or configurable ordinary-request concurrency ceiling. Codex owns
per-session concurrency and each provider owns its actual quota. Each request
SHALL check that provider's cooldown before remote I/O. HTTP 429 SHALL remain
terminal for the current request and SHALL NOT introduce an upstream retry.
Active-request accounting exists only for lifecycle handoff and observation.

#### Scenario: Concurrent requests target one provider route

- **WHEN** two Responses requests overlap on the same configured provider route
- **THEN** neither request waits in a proxy-owned concurrency queue
- **AND** both may perform provider I/O unless a provider cooldown is active.

#### Scenario: Different provider routes overlap

- **WHEN** Responses requests overlap on two different configured provider
  routes
- **THEN** both routes can perform provider I/O concurrently
- **AND** neither a route-level nor process-wide ordinary admission slot or
  queue exists.

#### Scenario: One request establishes cooldown

- **WHEN** a later request arrives after a preceding request recorded a
  provider rate-limit cooldown
- **THEN** the later request receives the existing local HTTP 429 response
- **AND** the proxy makes no upstream call for that later request.

#### Scenario: A lifecycle transition is in progress

- **WHEN** an upgrade, rollback or reload hands the listener off
- **THEN** new Responses are admitted and counted as at any other time
- **AND** active-request accounting stays an observation and never becomes a
  traffic limit.

## REMOVED Requirements

### Requirement: Ordinary concurrency remains outside the proxy

**Reason**: Its admission rule named a lifecycle drain barrier and a local draining response, which the product no longer has.

**Migration**: "The proxy owns no ordinary concurrency limit" keeps every other statement of it.
