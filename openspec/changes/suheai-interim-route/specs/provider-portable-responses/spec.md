## MODIFIED Requirements

### Requirement: Provider routes admit only exact Responses and model-catalog targets

The listener SHALL expose provider-scoped loopback Responses targets and exact
read-only model-catalog targets for `dmxapi`, `ucloud`, `aihubmix`, and
`suheai`. Each namespace SHALL resolve only exact, lexically normalized
Responses and model catalog paths against the release-owned HTTPS upstream
mapping. A downstream request SHALL NOT supply or override an upstream URL or
host.

#### Scenario: A client selects each governed provider

- **WHEN** a client sends otherwise equivalent Responses traffic to
  `/dmxapi/v1/responses`, `/ucloud/v1/responses`,
  `/aihubmix/v1/responses`, or `/suheai/v1/responses`
- **THEN** the proxy sends it only to the matching DMXAPI, UCloud, AIHubMix,
  or Suhe.AI HTTPS upstream
- **AND** credentials continue to come from that client request.

#### Scenario: A caller uses an unknown namespace

- **WHEN** a request path matches neither a control endpoint nor one of the
  listener's provider namespaces
- **THEN** the proxy rejects it locally
- **AND** it performs no remote network request.

#### Scenario: A caller uses a malformed provider target

- **WHEN** a target contains encoded path material, dot segments, duplicate
  separators, a lookalike suffix, a fragment, or an absolute URL
- **THEN** the proxy rejects it locally
- **AND** unrelated endpoints remain unreachable.

#### Scenario: A consumer reads one provider model catalog

- **WHEN** it sends GET to `/dmxapi/v1/models`, `/ucloud/v1/models`,
  `/aihubmix/v1/models`, or `/suheai/v1/models`
- **THEN** the proxy relays exactly one GET to the matching provider
- **AND** it does not activate Responses projection, cooldown, retry, or
  recovery.

#### Scenario: A rejected route carries an unread body

- **WHEN** an unknown route or unsupported method is rejected before its body
  is consumed
- **THEN** the response closes the connection
- **AND** the unread body is not parsed as another request.

### Requirement: Provider-specific recovery is route-scoped

The exact DMXAPI HTTP 477 `empty_response` recovery and cooldown SHALL apply
only to the DMXAPI route. A failure or cooldown key from DMXAPI SHALL NOT block,
rewrite, or classify a request on another provider route. Generic transient and
schema recovery MAY remain shared only where its trigger is provider-neutral
and exact.

#### Scenario: DMXAPI enters empty-response cooldown

- **WHEN** a DMXAPI request exhausts its exact HTTP 477 recovery and the same
  portable body is then sent to another provider route
- **THEN** the other provider request is attempted normally
- **AND** no DMXAPI cooldown response is reused across the route boundary.

#### Scenario: A non-DMX provider returns HTTP 477

- **WHEN** a provider other than DMXAPI returns HTTP 477
- **THEN** the proxy does not apply the DMXAPI `empty_response` policy unless
  the request was routed to DMXAPI and the exact DMX error contract matched.

#### Scenario: DMXAPI exhausts empty-response recovery

- **WHEN** DMXAPI yields the exact classified empty response through the bounded retry policy
- **THEN** the proxy emits a typed terminal error only after the budget is exhausted
- **AND** every other provider route remains independently usable.

### Requirement: Conversation state remains owned by Codex

The compatibility path SHALL NOT edit Codex JSONL, SQLite, transcript history,
archived conversations, hidden resume pointers, or per-conversation model
metadata. Success SHALL require an unchanged original conversation to complete
multiple turns after a provider sequence that starts on DMXAPI, visits every
other provider route, and returns to DMXAPI.

#### Scenario: The proxy fix is accepted

- **WHEN** the released proxy is installed, an external client selects each
  route, and the same original conversation completes at least two turns on each leg of
  that sequence
- **THEN** no turn reports missing `rs_` items, encrypted-content decode or
  decrypt failure, or proxy-generated empty-response 503
- **AND** acceptance evidence confirms that the exact pre-acceptance JSONL byte
  prefix remains unchanged while normal replies append only a suffix
- **AND** the observed SQLite stores and per-conversation model metadata remain
  unchanged by the repair.

### Requirement: Client endpoint ownership is external

The proxy SHALL expose provider-scoped loopback Responses endpoints without
reading, writing, backing up, or restoring client configuration. It SHALL NOT
execute a client-control command or persist consumer route state.

#### Scenario: A consumer selects a provider endpoint

- **WHEN** a client selects one provider-scoped endpoint, such as `/dmxapi/v1`,
  through its own control plane
- **THEN** the proxy serves that data-plane request without depending on the
  consumer package, configuration path, credential store, or projection model.

#### Scenario: The proxy is installed or removed

- **WHEN** installation, status, reload, or uninstall runs
- **THEN** only the proxy's released payload, product-owned state, listener,
  and native supervision are observed or mutated
- **AND** consumer endpoint configuration is unchanged.

### Requirement: Provider rate limits do not multiply across retry layers

For an upstream HTTP 429, the proxy SHALL make no additional upstream attempt
for the current client request. It SHALL relay the first upstream status, body,
and eligible non-hop-by-hop headers unchanged without sleeping. It SHALL record
a bounded provider-scoped cooldown and SHALL NOT infer or configure an
undocumented provider quota.

#### Scenario: A provider returns a rate limit

- **WHEN** one provider Responses attempt returns HTTP 429
- **THEN** the proxy relays that exact status, body, and eligible headers after
  exactly one upstream call
- **AND** it performs neither the generic proxy retry sleep nor another upstream
  request for that client attempt.

#### Scenario: The provider supplies bounded retry timing

- **WHEN** a provider returns HTTP 429 with valid delta-seconds or HTTP-date
  `Retry-After` no greater than five minutes
- **THEN** later Responses for only that provider receive local HTTP 429 until
  the interpreted process-local deadline expires
- **AND** the original response retains the provider's exact header.

#### Scenario: The provider supplies no usable retry timing

- **WHEN** a provider omits `Retry-After` or supplies an invalid, zero, or
  expired value
- **THEN** later Responses for that provider receive local HTTP 429 for the
  five-second fallback without opening an upstream connection
- **AND** another configured provider remains unaffected.

#### Scenario: The provider supplies excessive retry timing

- **WHEN** a provider supplies a positive `Retry-After` above five minutes
- **THEN** the process-local cooldown is capped at five minutes.

#### Scenario: One provider is rate-limited

- **WHEN** one provider records an active provider-scoped cooldown
- **THEN** no other provider inherits that cooldown or loses ordinary concurrency
- **AND** client-owned per-session concurrency remains outside the proxy.
