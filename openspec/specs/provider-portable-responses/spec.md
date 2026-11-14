# Provider-portable Responses

## Purpose

Provide one fail-closed Responses compatibility boundary that makes stored
Codex dialogue replayable across the governed DMXAPI, UCloud, and
AIHubMix routes without changing the conversation record itself.

## Requirements

### Requirement: Every Responses request is projected to a provider-portable form

Before upstream I/O, the proxy SHALL derive portability only from the current
request and the proved protocol grammar. It SHALL remove provider-bound
continuation state, stored-item references, replayed reasoning items, and
encrypted replay content. It SHALL set `store=false` and remove any request for
`reasoning.encrypted_content` while leaving provider-neutral generation settings
unchanged.

#### Scenario: A stored conversation changes provider

- **WHEN** a request contains `previous_response_id`, `conversation`,
  `prompt_cache_key`, an `rs_*` item, encrypted output, or another
  provider-owned continuation structure
- **THEN** none of that provider-bound state is sent to the selected upstream
- **AND** the upstream receives `store=false` with the remaining portable
  dialogue, complete tool history, and supported controls
- **AND** no client setting, JSONL, SQLite, history item, or model metadata is
  read or modified.

#### Scenario: A new request has no replay state

- **WHEN** a valid Responses request contains only provider-neutral input and
  generation settings
- **THEN** the projection preserves those semantics, sets `store=false`, and
  does not manufacture a continuation identifier, stored item, or decrypted
  value.

#### Scenario: Portable content is projected

- **WHEN** canonical dialogue, complete tool relationships, payload-free
  compaction controls, or supported non-text agent content is present
- **THEN** the projection keeps it representable
- **AND** every bounded recovery preserves the same projection
- **AND** no client configuration or conversation store is consulted or changed.

#### Scenario: Codex requests remote compaction

- **WHEN** Codex appends the payload-free `{"type":"compaction_trigger"}`
  request control to portable dialogue
- **THEN** the projection preserves that exact control item for the upstream
  Responses compaction request
- **AND** any additional field on that control is rejected locally as an
  unproved request shape.

#### Scenario: A conversation changes providers repeatedly

- **WHEN** a subsequent request switches among UCloud, DMXAPI, and AIHubMix
- **THEN** the outbound request uses `store=false`
- **AND** no `rs_*`, provider item identifier, or unproved provider-specific replay structure crosses the provider boundary
- **AND** portable user and agent content remains replayable.

### Requirement: Portable dialogue and tool relationships are preserved

The proxy SHALL preserve textual system, developer, user, and assistant
dialogue; agent author, recipient, and phase context; complete
function/custom-tool call-output pairs; and standalone cross-task tool delivery
results whose portable provenance is explicit.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Assistant, synthesized-agent,
  and standalone delivery history SHALL use provider-neutral Easy Input Message
  strings. System, developer, user, and paired tool-output lists SHALL use
  input-content grammar. Provider IDs, statuses, annotations, namespaces, and
  opaque metadata SHALL NOT be required by a paired output's outbound form.

#### Scenario: Text and paired calls are replayed

- **WHEN** a request contains text messages, an agent message, a function call
  and output, and a custom-tool call and output
- **THEN** the upstream receives equivalent role-valid portable text and both
  complete call-output pairs
- **AND** every paired output retains the matching `call_id` and call kind.

#### Scenario: Namespaced function output is replayed

- **WHEN** a valid function output follows its matching call and carries the
  optional namespace metadata emitted by Codex
- **THEN** the upstream receives the complete provider-portable call-output pair
- **AND** the namespace metadata is not required or forwarded
- **AND** any other unproved output field is still rejected before upstream I/O.

#### Scenario: Standalone cross-task delivery is replayed

- **WHEN** Codex replays a standalone function output with a non-empty item ID,
  tool name, namespace, and visible output but no `call_id`
- **THEN** the upstream receives one provider-neutral assistant message that
  preserves the tool name, namespace, and visible output
- **AND** the proxy does not invent a function call, call identity, or
  provider-bound continuation.

#### Scenario: Assistant content is normalized for replay

- **WHEN** an assistant message or projected agent message contains
  `input_text`, `output_text`, or refusal content from stored history
- **THEN** its portable assistant representation uses a deterministic string
  that preserves the visible text and phase
- **AND** it does not require output-item ID, status, annotation, or typed
  output content from the prior provider.

#### Scenario: Instruction and user content remain input

- **WHEN** a system, developer, or user message contains typed text from stored
  history
- **THEN** its portable representation uses `input_text`
- **AND** no assistant-only output block is emitted for that role.

#### Scenario: An agent or tool output has only opaque ciphertext

- **WHEN** removing encrypted content would otherwise leave an agent message or
  tool output empty
- **THEN** the proxy inserts a stable plaintext omission marker using the
  assistant string carrier or tool-output input grammar
- **AND** it does not claim to have decrypted or reconstructed the omitted
  result.

#### Scenario: Classified DMX retry preserves the projected bytes

- **WHEN** the normal provider-portable request receives the exact classified
  DMX empty-response error
- **THEN** the proxy retries the current projected attempt bytes exactly once
- **AND** it does not rebuild replay, restore an older request body, or recreate
  a provider-bound assistant typed-block shape.

#### Scenario: Replay contains an inline screenshot

- **WHEN** image-capable input contains a nonempty, strictly Base64-encoded
  PNG, JPEG, WebP, or GIF data URL
- **THEN** projection retains its original URL, detail, and order in dialogue
  and paired tool-output input, including image-only content
- **AND** repeated projection and classified retries preserve the image bytes
- **AND** validation performs no filesystem access, network fetch, raster
  decoding, or image re-encoding.

#### Scenario: Classified DMX retry retains replayable input images

- **WHEN** the normal portable request contains a validated remote or inline
  `input_image` in system, developer, user, or tool-output input content and
  receives the exact classified DMX empty-response error
- **THEN** the byte-identical retry preserves that image on input grammar
- **AND** it does not turn valid non-text input into a local exhausted 503.

#### Scenario: Recovery contains non-text agent content

- **WHEN** a recoverable response contains valid non-text agent items
- **THEN** recovery preserves their provider-portable semantic representation
- **AND** does not fabricate text or require provider-bound identifiers.

### Requirement: Paired empty tool results remain explicit

A correctly paired function or custom-tool result whose exact output is the
empty string SHALL retain its call kind and `call_id` and SHALL use one stable
plaintext empty-result marker in the outbound request copy. The exception SHALL
NOT apply to ordinary dialogue, missing or null output, or an invalid pair. A
Codex-generated assistant placeholder consisting of exactly one valid, empty
`output_text` block SHALL be treated as non-semantic history and omitted rather
than forwarded or rejected.

#### Scenario: A paired tool result is textually empty

- **WHEN** a valid function or custom-tool output follows its matching call and
  its exact result is the empty string
- **THEN** the outbound request retains the pair with the fixed empty-result
  marker
- **AND** it is not rejected as an empty dialogue message.

#### Scenario: A replay contains an empty assistant placeholder

- **WHEN** Codex replay contains an assistant message whose content is exactly
  one valid `output_text` block with an empty string
- **THEN** the proxy omits that non-semantic item before upstream I/O
- **AND** preserves every later provider-portable item in order.

#### Scenario: Empty ordinary dialogue remains invalid

- **WHEN** a system, developer, user, or synthesized dialogue message contains
  no portable text, or an assistant message has any other empty shape
- **THEN** the proxy rejects the shape locally
- **AND** neither the empty-tool-result nor placeholder exception applies.

### Requirement: Unproved replay shapes fail closed

Before upstream I/O, the proxy SHALL classify each Responses input item through
one authoritative item policy shared by diagnostics and provider-portable
projection.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Malformed JSON, invalid input containers, genuinely unknown replay
  item types, unknown content block types, orphaned or mismatched tool outputs,
  duplicate call/output identities, invalid required fields, and incomplete local
  shell pairs SHALL be rejected locally. The error SHALL identify a bounded
  structural reason without returning request text, credentials, or encrypted
  payloads.

#### Scenario: A future client introduces an unknown replay item

- **WHEN** the input list contains a replay item not recognized by the
  authoritative item policy
- **THEN** the proxy returns a local client error identified as an unknown item
- **AND** no configured provider receives the request.

#### Scenario: A recognized client item lacks portable semantics

- **WHEN** the authoritative policy recognizes an item emitted by a supported
  client but does not define a safe provider-portable projection for it
- **THEN** the proxy returns a local client error identified as bounded schema
  drift rather than an unknown item
- **AND** no configured provider receives the request.

#### Scenario: A tool output is not safely paired

- **WHEN** an output precedes its call, names an unknown `call_id`, duplicates
  an earlier output, or does not match the call kind
- **THEN** the request is rejected rather than silently deleting, reordering,
  or inventing tool history.

#### Scenario: Codex local shell history is provider-local

- **WHEN** replay contains a `local_shell_call` with a valid closed `exec`
  action, a supported status, exactly one matching `function_call_output`, and
  a current dialogue item
- **THEN** the proxy removes the complete local shell pair before upstream I/O
- **AND** it preserves the current dialogue item
- **AND** an unpaired call, invalid status, unknown action field, or invalid
  action value is rejected locally.

#### Scenario: Diagnostic and projection classification agree

- **WHEN** the proxy diagnoses and projects the same Responses input item
- **THEN** both operations consume the same authoritative item classification
- **AND** a recognized item cannot fall through projection as an unknown type.

### Requirement: Provider routes admit only exact Responses and model-catalog targets

The listener SHALL expose provider-scoped loopback Responses targets and exact
read-only model-catalog targets for `dmxapi`, `ucloud`, and `aihubmix`. Each
namespace SHALL resolve only exact, lexically normalized Responses and model
catalog paths against the release-owned HTTPS upstream mapping. A downstream
request SHALL NOT supply or override an upstream URL or host.

#### Scenario: A client selects each governed provider

- **WHEN** a client sends otherwise equivalent Responses traffic to
  `/dmxapi/v1/responses`, `/ucloud/v1/responses`, or
  `/aihubmix/v1/responses`
- **THEN** the proxy sends it only to the matching DMXAPI, UCloud, or
  AIHubMix HTTPS upstream
- **AND** credentials continue to come from that client request.

#### Scenario: A caller uses an unknown namespace

- **WHEN** a request path matches neither a control endpoint nor one of the
  three canonical namespaces
- **THEN** the proxy rejects it locally
- **AND** it performs no remote network request.

#### Scenario: A caller uses a malformed provider target

- **WHEN** a target contains encoded path material, dot segments, duplicate
  separators, a lookalike suffix, a fragment, or an absolute URL
- **THEN** the proxy rejects it locally
- **AND** unrelated endpoints remain unreachable.

#### Scenario: A consumer reads one provider model catalog

- **WHEN** it sends GET to `/dmxapi/v1/models`, `/ucloud/v1/models`, or
  `/aihubmix/v1/models`
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
rewrite, or classify a UCloud or AIHubMix request. Generic transient and
schema recovery MAY remain shared only where its trigger is provider-neutral
and exact.

#### Scenario: DMXAPI enters empty-response cooldown

- **WHEN** a DMXAPI request exhausts its exact HTTP 477 recovery and the same
  portable body is then sent to UCloud or AIHubMix
- **THEN** the other provider request is attempted normally
- **AND** no DMXAPI cooldown response is reused across the route boundary.

#### Scenario: A non-DMX provider returns HTTP 477

- **WHEN** UCloud or AIHubMix returns HTTP 477
- **THEN** the proxy does not apply the DMXAPI `empty_response` policy unless
  the request was routed to DMXAPI and the exact DMX error contract matched.

#### Scenario: DMXAPI exhausts empty-response recovery

- **WHEN** DMXAPI yields the exact classified empty response through the bounded retry policy
- **THEN** the proxy emits a typed terminal error only after the budget is exhausted
- **AND** UCloud and AIHubMix remain independently usable.

### Requirement: Live response control data survives the current turn

For Responses event streams, the proxy SHALL preserve upstream event bytes,
including encrypted reasoning, agent, and tool-control data needed by Codex to
complete the current turn. Provider portability SHALL be enforced only when a
later request replays prior output. JSON-bearing data events SHALL be validated
atomically before downstream commitment.

#### Scenario: An upstream emits encrypted agent content

- **WHEN** a streamed output item contains plaintext plus encrypted agent or
  tool-output blocks
- **THEN** downstream receives the complete event unchanged so Codex can decrypt
  and dispatch the current collaboration action
- **AND** a later outbound replay removes that provider-bound ciphertext before
  another provider receives it.

### Requirement: Non-stream Responses are validated before commitment

Successful non-stream Responses SHALL be buffered within an eight-MiB limit
before downstream HTTP commitment, required to be a valid `completed` or
`incomplete` Response JSON document, and preserved byte-for-byte for the
current turn. Empty, truncated, oversized, malformed,
failed, or otherwise non-terminal HTTP 2xx bodies SHALL produce a local
retryable failure without committing partial upstream bytes.

#### Scenario: A successful JSON response contains provider ciphertext

- **WHEN** a terminal non-stream Response contains visible output plus opaque
  reasoning, agent, or tool-output ciphertext
- **THEN** downstream receives the complete terminal Response unchanged
- **AND** Codex can decrypt and execute the current-turn control payload
- **AND** the response is committed with a correct bounded content length.

#### Scenario: HTTP 2xx does not contain a proved terminal Response

- **WHEN** the body is empty, truncated, malformed, failed, in progress, or
  missing a terminal status
- **THEN** downstream receives a local retryable failure
- **AND** no upstream success status or partial body has been committed.

#### Scenario: A successful response exceeds the integrity bound

- **WHEN** an upstream HTTP 2xx body exceeds eight MiB before terminal JSON is
  proved
- **THEN** the proxy returns a bounded retryable local failure
- **AND** no upstream bytes are committed as success.

### Requirement: Request-changing recovery uses structured errors

Recovery that changes the projected request SHALL be admitted only from a
bounded structured error contract. Human-readable message text alone SHALL NOT
activate compaction, dialogue recovery, or schema fallback.

#### Scenario: Error prose contains a known phrase

- **WHEN** a structured error has an unrelated type or code but its message
  mentions `response_failed`, `request blocked`, or another recovery phrase
- **THEN** the proxy relays or classifies it without changing replay.

### Requirement: Conversation state remains owned by Codex

The compatibility path SHALL NOT edit Codex JSONL, SQLite, transcript history,
archived conversations, hidden resume pointers, or per-conversation model
metadata. Success SHALL require an unchanged original conversation to complete
multiple turns after the provider sequence DMXAPI, UCloud, AIHubMix, and
DMXAPI again.

#### Scenario: The proxy fix is accepted

- **WHEN** the released proxy is installed, an external client selects each
  route, and the same original conversation completes at least two turns on each leg of
  DMXAPI to UCloud to AIHubMix to DMXAPI
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

- **WHEN** a client selects `/dmxapi/v1`, `/ucloud/v1`, or
  `/aihubmix/v1` through its own control plane
- **THEN** the proxy serves that data-plane request without depending on the
  consumer package, configuration path, credential store, or projection model.

#### Scenario: The proxy is installed or removed

- **WHEN** installation, status, reload, or uninstall runs
- **THEN** only the proxy's released payload, product-owned state, listener,
  and native supervision are observed or mutated
- **AND** consumer endpoint configuration is unchanged.

### Requirement: Responses admission is closed at the HTTP boundary

Every POST Responses request SHALL pass through fail-closed request projection,
including a zero-length body. Provider-scoped routes SHALL resolve only exact,
lexically normalized `/v1/responses` targets with an optional query.

#### Scenario: A caller sends an empty Responses request

- **WHEN** a POST Responses request has no body
- **THEN** the proxy rejects it locally as invalid JSON
- **AND** no configured provider receives the request.

#### Scenario: A caller sends an ambiguous route suffix

- **WHEN** the route contains dot segments, encoded path material, duplicate
  separators, a lookalike suffix, or a non-Responses endpoint
- **THEN** the proxy rejects it locally
- **AND** it does not construct or contact an upstream URL.

### Requirement: Provider wire differences are isolated

The provider manifest MAY select one optional pure wire-policy module. Core
transport SHALL depend only on that policy contract and SHALL NOT branch on a
provider name. A provider with no wire difference SHALL require only one
secret-free manifest entry. A policy MUST NOT perform HTTP dispatch, retries,
credential access, host discovery, filesystem access, runtime mutation,
lifecycle operations, or client configuration.

#### Scenario: A new ordinary provider is added

- **WHEN** a synthetic provider's Responses wire behavior matches the
  provider-neutral core
- **THEN** setup requires only its declarative manifest record
- **AND** no listener, relay, lifecycle, CLI, release, service, consumer,
  replay, or registry provider-name branch is added.

#### Scenario: A provider has a proved special wire contract

- **WHEN** executable fixtures prove a distinct request, response, or error
  contract
- **THEN** one manifest-selected policy implements only that transformation or
  classification
- **AND** every higher layer continues to depend on the policy contract rather
  than provider identity.

### Requirement: Recovery classifiers use structured provider errors

Any recovery that changes the request body SHALL be triggered only by a bounded
structured error contract. Incidental human-readable message text SHALL NOT be
sufficient to activate semantic recovery.

#### Scenario: Ordinary error prose contains a recovery phrase

- **WHEN** an upstream error message contains a known phrase but its structured
  type and code do not match the recovery contract
- **THEN** the proxy relays or classifies the error without changing replay.

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

- **WHEN** UCloud, DMXAPI, or AIHubMix records an active provider-scoped cooldown
- **THEN** no other provider inherits that cooldown or loses ordinary concurrency
- **AND** client-owned per-session concurrency remains outside the proxy.

### Requirement: Active provider cooldown deadlines do not move backward

For one cooldown key, a repeated or concurrent failure SHALL NOT replace a
still-active deadline with an earlier deadline. The proxy SHALL retain the later
of the current and newly computed deadlines under the existing synchronized
cache owner. It SHALL continue to purge expired entries, bound cache capacity,
and isolate unrelated provider and request-fingerprint keys.

#### Scenario: A shorter rate limit follows a longer active instruction

- **WHEN** one provider has an active 300-second cooldown and a later failure
  computes a five-second cooldown before the first deadline expires
- **THEN** the stored deadline remains the original later deadline
- **AND** upstream traffic for that provider is not reopened by the shorter
  overlapping failure.

#### Scenario: Another key receives a shorter deadline

- **WHEN** an unrelated provider or request fingerprint records a shorter
  cooldown
- **THEN** its deadline is stored independently
- **AND** the longer key is neither shortened nor copied across the boundary.

### Requirement: Ordinary concurrency remains outside the proxy

The proxy SHALL NOT serialize a provider route, queue ordinary traffic, or own
a fixed or configurable ordinary-request concurrency ceiling. Codex owns
per-session concurrency and each provider owns its actual quota. Each request
SHALL check that provider's cooldown and the lifecycle drain barrier before
remote I/O. HTTP 429 SHALL remain terminal for the current request and SHALL NOT
introduce an upstream retry. Active-request accounting exists only for
lifecycle handoff and observation.

#### Scenario: Concurrent requests target one provider route

- **WHEN** two Responses requests overlap on the same configured provider route
- **THEN** neither request waits in a proxy-owned concurrency queue
- **AND** both may perform provider I/O unless a provider cooldown or lifecycle
  drain is active.

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

#### Scenario: Lifecycle drain closes admission

- **WHEN** a transactional reload or shutdown activates the drain barrier
- **THEN** new Responses receive the bounded local draining response
- **AND** active-request accounting remains available for safe handoff without
  becoming an ordinary traffic limit.

### Requirement: A top-level error event ends the stream

A top-level `error` event of a Responses stream SHALL be a terminal event: it
SHALL end the stream as `response.completed`, `response.failed` and
`response.incomplete` do, and it SHALL commit the response as content. The
client SHALL receive the event unchanged and a complete HTTP body, and the
request SHALL NOT be sent again. Diagnostics SHALL name the ending without the
event's message.

#### Scenario: An upstream refuses a request inside the stream

- **WHEN** an upstream sends `response.created`, `response.in_progress` and a
  top-level `error` event, then closes
- **THEN** the client receives those events unchanged, and the chunked body ends
- **AND** the proxy does not reopen the request
- **AND** the log names `terminal=error` and the event's code, read beside the
  event's type or, when none or `null` stands there, in its `error` member,
  when the code is a token of lowercase letters, digits and underscores of at
  most 64 characters, `unknown` otherwise, and never the event's message.

#### Scenario: An upstream sends more after its error event

- **WHEN** an upstream sends a top-level `error` event and then further events
- **THEN** the client receives nothing after the `error` event
- **AND** the chunked body ends.

### Requirement: An early stream ending names where it stopped

Diagnostics of a Responses stream that ends without a terminal event SHALL name
the type of the last event that the proxy accepted, as protocol vocabulary and
never as content. The log's redaction of credential-shaped words SHALL apply to
a code and to an event type as to every other word of the log.

#### Scenario: A stream ends without a terminal event

- **WHEN** an upstream stream ends without a terminal event
- **THEN** a response whose headers were sent is closed without the end of its
  chunked body
- **AND** the log names the type of the last accepted event when it is a token
  of lowercase letters, digits, dots and underscores of at most 64 characters,
  `untyped` for an event without a type, `other` for another type and `none`
  for a stream without an accepted event.

#### Scenario: A code is a credential word

- **WHEN** an `error` event's code is a word that the log redacts together with
  the word after it, such as `authorization`
- **THEN** the line keeps its event count and its path
- **AND** a code that begins with such a word, such as `authorization_error`, is
  shown as redacted.
