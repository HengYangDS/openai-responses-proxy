## ADDED Requirements

### Requirement: Upstream addressing admits only exact Responses and model-catalog targets

The listener SHALL accept a data request only at
`/<scheme>/<authority>/<segment>*/<resource>` with an optional query, where the
resource is `responses` or `models`, and SHALL forward it to
`<scheme>://<authority>/<segment>*/<resource>` with that query. The scheme
SHALL be `https`, or `http` for a loopback authority only. The authority SHALL
carry no user information, and the quoted path SHALL equal the path. A data
request SHALL carry a loopback `Host` and no `Origin`.

#### Scenario: A client names an HTTPS upstream

- **WHEN** a client sends Responses traffic to `/https/api.example.com/v1/responses`
- **THEN** the proxy sends it only to `https://api.example.com/v1/responses`
- **AND** credentials continue to come from that client request.

#### Scenario: A consumer reads a model catalog

- **WHEN** it sends GET to `/https/api.example.com/v1/models?client_version=1`
- **THEN** the proxy relays exactly one GET to
  `https://api.example.com/v1/models?client_version=1`
- **AND** it does not activate Responses projection, cooldown, retry, or
  recovery.

#### Scenario: A caller uses a malformed upstream target

- **WHEN** a target contains encoded path material, a dot or empty segment,
  user information, a scheme other than lower-case `https` or `http`, a
  fragment, an absolute URL, or a resource other than `responses` or `models`
- **THEN** the proxy rejects it locally with `upstream_route_invalid`
- **AND** it performs no remote network request.

#### Scenario: Plain HTTP names a remote host

- **WHEN** a target's scheme is `http` and its authority is not a loopback
  address
- **THEN** the proxy rejects it locally with `upstream_route_invalid`.

#### Scenario: A web page sends a data request

- **WHEN** a data request carries an `Origin` header or a non-loopback `Host`
- **THEN** the proxy refuses it with HTTP 403 before resolving its path
- **AND** it performs no remote network request.

#### Scenario: A rejected route carries an unread body

- **WHEN** an invalid target or unsupported method is rejected before its body
  is consumed
- **THEN** the response closes the connection
- **AND** the unread body is not parsed as another request.

### Requirement: The proxy names no provider

The released payload SHALL name no provider or provider endpoint, and no module
SHALL branch on an upstream's host or name. Behavior that differs by upstream
SHALL be keyed by its upstream origin: the scheme, authority and base path,
never the query. A new upstream SHALL need no release. Log lines and cooldown
keys SHALL name the upstream origin.

#### Scenario: A client names a new upstream

- **WHEN** a client names an HTTPS upstream that no earlier request named
- **THEN** the proxy serves it through the existing Responses path without a
  release, manifest entry, or configuration
- **AND** its log lines record `upstream=` with the origin and without the query.

#### Scenario: An upstream returns HTTP 477

- **WHEN** an upstream answers a Responses request with HTTP 477
- **THEN** the proxy relays it like any other client error after one upstream
  call
- **AND** it records no cooldown.

## MODIFIED Requirements

### Requirement: Conversation state remains owned by Codex

The compatibility path SHALL NOT edit Codex JSONL, SQLite, transcript history,
archived conversations, hidden resume pointers, or per-conversation model
metadata. Success SHALL require an unchanged original conversation to complete
multiple turns after an upstream sequence that starts on one upstream origin,
visits every other origin the client uses, and returns to the first.

#### Scenario: The proxy fix is accepted

- **WHEN** the released proxy is installed, an external client selects each
  upstream origin, and the same original conversation completes at least two turns on each leg of
  that sequence
- **THEN** no turn reports missing `rs_` items, encrypted-content decode or
  decrypt failure, or proxy-generated empty-response 503
- **AND** acceptance evidence confirms that the exact pre-acceptance JSONL byte
  prefix remains unchanged while normal replies append only a suffix
- **AND** the observed SQLite stores and per-conversation model metadata remain
  unchanged by the repair.

### Requirement: Client endpoint ownership is external

The proxy SHALL serve the upstream that a client's loopback base URL names without
reading, writing, backing up, or restoring client configuration. It SHALL NOT
execute a client-control command or persist consumer route state.

#### Scenario: A consumer selects a provider endpoint

- **WHEN** a client's own control plane writes a base URL such as
  `http://127.0.0.1:<port>/https/api.example.com/v1`
- **THEN** the proxy serves that data-plane request without depending on the
  consumer package, configuration path, credential store, or projection model.

#### Scenario: The proxy is installed or removed

- **WHEN** installation, status, reload, or uninstall runs
- **THEN** only the proxy's released payload, product-owned state, listener,
  and native supervision are observed or mutated
- **AND** consumer endpoint configuration is unchanged.

### Requirement: Responses admission is closed at the HTTP boundary

Every POST Responses request SHALL pass through fail-closed request projection,
including a zero-length body. Upstream addressing SHALL resolve only exact,
lexically normalized Responses targets with an optional query.

#### Scenario: A caller sends an empty Responses request

- **WHEN** a POST Responses request has no body
- **THEN** the proxy rejects it locally as invalid JSON
- **AND** no upstream receives the request.

#### Scenario: A caller sends an ambiguous route suffix

- **WHEN** the route contains dot segments, encoded path material, duplicate
  separators, a lookalike suffix, or a non-Responses endpoint
- **THEN** the proxy rejects it locally
- **AND** it does not construct or contact an upstream URL.

### Requirement: Provider rate limits do not multiply across retry layers

For an upstream HTTP 429, the proxy SHALL make no additional upstream attempt
for the current client request. It SHALL relay the first upstream status, body,
and eligible non-hop-by-hop headers unchanged without sleeping. It SHALL record
a bounded cooldown for that upstream origin and SHALL NOT infer or configure an
undocumented provider quota.

#### Scenario: A provider returns a rate limit

- **WHEN** one Responses attempt returns HTTP 429
- **THEN** the proxy relays that exact status, body, and eligible headers after
  exactly one upstream call
- **AND** it performs neither the generic proxy retry sleep nor another upstream
  request for that client attempt.

#### Scenario: The provider supplies bounded retry timing

- **WHEN** a provider returns HTTP 429 with valid delta-seconds or HTTP-date
  `Retry-After` no greater than five minutes
- **THEN** later Responses for only that upstream origin receive local HTTP 429 until
  the interpreted process-local deadline expires
- **AND** the original response retains the provider's exact header.

#### Scenario: The provider supplies no usable retry timing

- **WHEN** a provider omits `Retry-After` or supplies an invalid, zero, or
  expired value
- **THEN** later Responses for that upstream origin receive local HTTP 429 for the
  five-second fallback without opening an upstream connection
- **AND** another upstream origin remains unaffected.

#### Scenario: The provider supplies excessive retry timing

- **WHEN** a provider supplies a positive `Retry-After` above five minutes
- **THEN** the process-local cooldown is capped at five minutes.

#### Scenario: One provider is rate-limited

- **WHEN** one upstream origin has an active cooldown
- **THEN** no other origin, including one on the same host with another base
  path, inherits that cooldown or loses ordinary concurrency
- **AND** client-owned per-session concurrency remains outside the proxy.

### Requirement: Active provider cooldown deadlines do not move backward

For one cooldown key, a repeated or concurrent failure SHALL NOT replace a
still-active deadline with an earlier deadline. The proxy SHALL retain the later
of the current and newly computed deadlines under the existing synchronized
cache owner. It SHALL continue to purge expired entries, bound cache capacity,
and isolate unrelated upstream-origin keys.

#### Scenario: A shorter rate limit follows a longer active instruction

- **WHEN** one upstream origin has an active 300-second cooldown and a later failure
  computes a five-second cooldown before the first deadline expires
- **THEN** the stored deadline remains the original later deadline
- **AND** upstream traffic for that origin is not reopened by the shorter
  overlapping failure.

#### Scenario: Another key receives a shorter deadline

- **WHEN** an unrelated upstream origin records a shorter cooldown
- **THEN** its deadline is stored independently
- **AND** the longer key is neither shortened nor copied across the boundary.

### Requirement: The proxy owns no ordinary concurrency limit

The proxy SHALL NOT serialize an upstream origin, queue ordinary traffic, or own
a fixed or configurable ordinary-request concurrency ceiling. Codex owns
per-session concurrency and each provider owns its actual quota. Each request
SHALL check its upstream origin's cooldown before remote I/O. HTTP 429 SHALL remain
terminal for the current request and SHALL NOT introduce an upstream retry.
Active-request accounting exists only for lifecycle handoff and observation.

#### Scenario: Concurrent requests target one provider route

- **WHEN** two Responses requests overlap on the same upstream origin
- **THEN** neither request waits in a proxy-owned concurrency queue
- **AND** both may perform upstream I/O unless that origin's cooldown is active.

#### Scenario: Different provider routes overlap

- **WHEN** Responses requests overlap on two different upstream origins
- **THEN** both can perform upstream I/O concurrently
- **AND** neither an origin-level nor process-wide ordinary admission slot or
  queue exists.

#### Scenario: One request establishes cooldown

- **WHEN** a later request arrives after a preceding request recorded a
  rate-limit cooldown for the same upstream origin
- **THEN** the later request receives the existing local HTTP 429 response
- **AND** the proxy makes no upstream call for that later request.

#### Scenario: A lifecycle transition is in progress

- **WHEN** an upgrade, rollback or reload hands the listener off
- **THEN** new Responses are admitted and counted as at any other time
- **AND** active-request accounting stays an observation and never becomes a
  traffic limit.

### Requirement: Sustained upstream unavailability does not amplify demand

Responses SHALL retain bounded request-local recovery for a transient upstream
`503`. When that recovery ends in `503`, the proxy SHALL relay the upstream
status and body and remember a bounded cooldown for that origin only.
During the cooldown, another Responses request for that origin SHALL receive
a typed local `503` with `Retry-After` before upstream I/O. The cooldown SHALL
not block another origin or non-Responses resource, and a request after expiry
SHALL probe the upstream again.

#### Scenario: A Provider remains unavailable across turns

- **WHEN** one Responses turn exhausts its bounded retries with upstream `503`
- **THEN** that turn receives the original terminal `503`
- **AND** an immediate subsequent turn for the same origin receives a local
  `provider_unavailable_cooldown` response without upstream I/O
- **AND** another origin remains usable; after expiry the first origin is
  probed again.

## REMOVED Requirements

### Requirement: Provider routes admit only exact Responses and model-catalog targets

**Reason**: The request path names the upstream, so the release no longer ships provider-named routes.

**Migration**: The client's control plane writes `http://127.0.0.1:<port>/https/<authority>/<base path>` as the base
URL; "Upstream addressing admits only exact Responses and model-catalog targets" states the rule.

### Requirement: Provider-specific recovery is route-scoped

**Reason**: The DMXAPI HTTP 477 recovery and its fingerprint cooldown are removed: the installed proxy logged no
HTTP 477 among 21,414 Responses requests from 2026-11-19 to 2026-11-27.

**Migration**: None. An HTTP 477 reaches the client like any other client error.

### Requirement: Provider wire differences are isolated

**Reason**: The provider manifest and its wire-policy package are removed.

**Migration**: A future wire difference is keyed by the response or the upstream origin, as "The proxy names no
provider" requires.

### Requirement: Provider extension is adapter-only

**Reason**: Replaced by "The proxy names no provider": a new upstream needs no manifest entry and no release.

**Migration**: A new provider needs only its base URL in the client's control plane.

### Requirement: Plaintext agent delivery is selected before generation

**Reason**: Measurement M4 on 2026-11-27 showed that UCloud, the one route that selected it, now handles Codex's
native encrypted collaboration messages.

**Migration**: None. Every upstream receives the client's native agent delivery.
