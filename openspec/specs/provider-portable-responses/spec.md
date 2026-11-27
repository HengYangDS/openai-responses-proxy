# Provider-portable Responses

## Purpose

Provide one fail-closed Responses compatibility boundary that makes stored
Codex dialogue replayable across the governed provider routes without
changing the conversation record itself.

## Requirements

### Requirement: Every Responses request is projected to a provider-portable form

Before upstream I/O, the proxy SHALL project the current request through one
proved portability grammar, preserving portable input and generation settings.
It SHALL remove provider-bound continuation and optional encrypted replay state,
set `store=false`, and omit requests for `reasoning.encrypted_content`. Required
encrypted agent tasks SHALL keep their native envelope and exact ciphertext;
upstream rejection SHALL NOT substitute a header-only task.

#### Scenario: A stored conversation changes provider

- **WHEN** a request contains `previous_response_id`, `conversation`,
  `prompt_cache_key`, stored-item references, replayed reasoning items such as
  `rs_*`, optional encrypted output, or another provider-owned continuation
  structure
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

#### Scenario: Codex declares tools for the current turn

- **WHEN** a request contains a valid `additional_tools` control with a tool
  catalog, including an explicitly empty catalog during native compaction
- **THEN** the projection preserves that current-turn control, including its
  declared role, catalog, and client item identity, rather than deleting it as
  auxiliary replay history
- **AND** malformed outer controls are rejected before upstream I/O
- **AND** shrinking recovery retains the tool catalog and compaction intent or
  declines recovery instead of silently changing the available tools.

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

- **WHEN** a subsequent request switches among provider routes
- **THEN** the outbound request uses `store=false`
- **AND** no optional reasoning or continuation binding crosses the provider boundary
- **AND** portable user and plaintext agent content remains replayable; encrypted
  delegation additionally requires an upstream capable of interpreting it.

### Requirement: Portable dialogue and tool relationships are preserved

The proxy SHALL preserve role-valid dialogue, agent author/recipient/phase,
complete function/custom-tool call-output pairs, and standalone deliveries with
explicit portable provenance. Assistant, plaintext synthesized-agent, and
standalone history SHALL use Easy Input Message strings; instructions, user
content, and paired output lists SHALL use input-content grammar. Paired outputs
SHALL NOT depend on provider IDs or opaque metadata.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** an admitted function or custom-tool output carries provider bookkeeping
- **THEN** its outbound pair keeps the call identity, kind, and visible output
  using input-content grammar
- **AND** the outbound form needs no provider item ID, status, annotation,
  namespace, or opaque metadata.

#### Scenario: Text and paired calls are replayed

- **WHEN** a request contains text messages, a plaintext agent message, a function call
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
- **AND** shared diagnosis and recovery classify it as independent delivery,
  not an orphaned pair; repeated item IDs across standalone and paired outputs
  remain local rejections, and custom-tool outputs do not inherit this shape.

#### Scenario: Assistant content is normalized for replay

- **WHEN** an assistant message or projected plaintext agent message contains
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

#### Scenario: An agent message carries an encrypted task

- **WHEN** a valid agent message contains nonempty encrypted task content,
  with or without a visible routing header
- **THEN** the original message kind, author, recipient, content order and
  ciphertext remain unchanged; local bookkeeping metadata is removed
- **AND** a missing, empty, malformed or unknown ciphertext field is rejected
  rather than replaced by a header-only task or a fabricated plaintext result
- **AND** shrinking recovery cannot discard this native control input.

#### Scenario: An agent or tool output has only opaque ciphertext

- **WHEN** an agent or ordinary paired tool output has no plaintext beyond
  its opaque ciphertext
- **THEN** the agent keeps its native encrypted envelope, while an ordinary
  historical tool output keeps its explicit omission marker in input grammar
- **AND** the proxy does not claim to decrypt or reconstruct that history. This
  behavior does not apply to required agent-task messages.

#### Scenario: A selected Provider cannot decrypt native delegation

- **WHEN** an encrypted agent task receives HTTP 400 with
  `type=invalid_request_error` and `code=invalid_encrypted_content`
- **THEN** the proxy relays that rejection without a second upstream request
- **AND** the original encrypted task and local conversation history remain
  unchanged; a visible routing header is never accepted as the task body.

#### Scenario: Classified DMX retry preserves the projected bytes

- **WHEN** the normal provider-portable request receives the exact classified
  DMX empty-response error
- **THEN** the proxy retries the current projected attempt bytes exactly once
- **AND** it does not rebuild replay, restore an older request body, or recreate
  a provider-bound assistant typed-block shape.

#### Scenario: Replay contains an inline screenshot

- **WHEN** image-capable input contains an HTTP(S) image URL or a nonempty,
  strictly Base64-encoded PNG, JPEG, WebP, or GIF data URL
- **THEN** the shared content projection retains its original URL, detail, and
  order in dialogue and paired tool-output input, including image-only content
- **AND** repeated projection and classified retries preserve the image bytes
  without increasing the omitted-image count
- **AND** validation performs no filesystem access, network fetch, raster
  decoding, or image re-encoding; local file references and malformed image
  transports retain their existing disposition.

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

One item/relationship policy SHALL govern diagnostics and pre-I/O projection.
Malformed JSON, invalid containers or required fields, unknown item
or content types, duplicate identities, unproved repeated outputs, orphaned or
mismatched outputs, and incomplete shell pairs SHALL fail locally. A distinct
later named text delivery matching the original call SHALL retain delivery
semantics. Errors SHALL expose only bounded structural reasons, never request
text, credentials, or ciphertext.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** a request contains malformed JSON, an invalid input container, an
  unknown item or content block type, or an invalid required field
- **THEN** the proxy rejects it before upstream I/O with a bounded structural
  reason
- **AND** neither the reason nor diagnostics expose request text, credentials,
  or ciphertext.

#### Scenario: A later delivery has a distinct identity

- **WHEN** a later named textual delivery has a distinct item identity and
  matches the original call
- **THEN** it retains its explicit delivery semantics rather than becoming a
  second paired output
- **AND** duplicate call identities, repeated output item identities, and
  unproved repeated outputs remain local rejections.

#### Scenario: A JSON number has no finite native representation

- **WHEN** a request contains `NaN`, `Infinity`, `-Infinity`, or a numeric
  exponent that overflows the native JSON number representation at any depth
- **THEN** projection and diagnosis report `invalid_json` before normalization
  or upstream I/O, including in otherwise removed provider state
- **AND** finite numbers and numeric-looking strings retain their semantics;
  a clean request retains its exact bytes.

#### Scenario: A future client introduces an unknown replay item

- **WHEN** the input list contains an item not recognized by the item policy
- **THEN** the proxy returns a local error identified as an unknown item
- **AND** no configured provider receives the request.

#### Scenario: A recognized client item lacks portable semantics

- **WHEN** the policy recognizes an item but has no safe portable projection
- **THEN** the proxy returns a bounded schema-drift error
- **AND** no configured provider receives the request.

#### Scenario: A tool output is not safely paired

- **WHEN** an output precedes its call, names an unknown `call_id`, repeats an
  output item identity, lacks required delivery provenance after the first
  result, or does not match the original call kind and name
- **THEN** the request is rejected without deleting, reordering or inventing
  tool history.

#### Scenario: Codex local shell history is provider-local

- **WHEN** replay contains a `local_shell_call` with a valid closed `exec`
  action, supported status, exactly one matching `function_call_output`, and
  a current dialogue item
- **THEN** the proxy removes the complete local shell pair before upstream I/O
- **AND** it preserves the dialogue; unpaired calls, invalid statuses, unknown
  action fields and invalid action values remain local rejections.

#### Scenario: Diagnostic and projection classification agree

- **WHEN** the proxy diagnoses and projects the same input item
- **THEN** both operations consume the same item and relationship policy.

#### Scenario: A semantic selector has an invalid JSON type

- **WHEN** a replay role, phase or shell status is an array or object
- **THEN** projection and diagnosis return the same bounded field rejection
  before upstream I/O, without a traceback or private content
- **AND** non-string terminal response statuses are rejected locally; selected
  delegation schemas and replay calls require string names and types
- **AND** valid string selectors, native encrypted content and unrelated live
  response bytes retain their existing semantics.

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

### Requirement: Upstream capacity rejection permits only safe shrinking recovery

Exact upstream capacity rejections SHALL permit one strictly smaller, dependency-safe historical compaction. Required current-turn content SHALL remain unchanged. Unsafe or rejected recovery SHALL retain the upstream error without further retry.

#### Scenario: Historical input consumes upstream capacity

- **WHEN** HTTP 400 carries exactly `type=invalid_request_error`, `code=validation_error`, an empty parameter, and `Input is too long` or `media inputs exceed the remaining encoded media budget after processing`, optionally followed only by its request ID
- **THEN** one smaller dependency-safe projection may be attempted, preserving current images, instructions and content; content-free diagnostics report the outcome.

#### Scenario: The active turn alone exceeds capacity

- **WHEN** the same-turn media or required input leaves no safe reduction
- **THEN** the exact upstream rejection reaches the client after one attempt, without removing images or consulting client history.

### Requirement: Responses semantics have one classification and projection authority

Request admission, replay diagnosis, relationship validation, Provider-portable
projection, and recovery classification SHALL consume one authoritative typed
item and relationship model. A recognized item SHALL have exactly one portable,
Provider-local, or rejected disposition; no sanitizer, adapter, diagnostic path,
or fallback SHALL reinterpret it independently.

#### Scenario: One replay enters the request path

- **WHEN** the replay is admitted, diagnosed, projected, retried, or rejected
- **THEN** every stage consumes the same item identities and relationships
- **AND** no duplicate classifier, silent deletion, invented history, or
  unknown-item fall-through changes its meaning.

#### Scenario: A classified failure requires a smaller current request

- **WHEN** validation or execution recovery removes historical input
- **THEN** both paths use the same typed current-turn selection to retain the
  latest system, developer and user messages, the input following that user,
  native turn controls and declared dependencies in their original order
- **AND** top-level instructions remain unchanged; recovery cannot reduce the
  request by discarding required instruction roles or native delegation.
- **AND** compaction checks the actual retained sequence and its declared
  dependencies, not a suffix that omits separately retained earlier input.

#### Scenario: An ordinary Responses message omits its optional type

- **WHEN** a standard input message supplies its role and content without the
  optional `type` discriminator
- **THEN** admission projects it through the same message owner as an explicit
  `type: message`, preserving role, content order and supported phase
- **AND** invalid roles, content and unknown fields retain their declared
  rejection; a present invalid discriminator is not treated as omission.

#### Scenario: Provider-local search history declares tool relationships

- **WHEN** replay contains a recognized Provider-local tool search call or output
- **THEN** projection, diagnosis and recovery use the shared ordered relationship
  boundary before the declared Provider-local items are removed
- **AND** missing identities, orphaned or preceding outputs, duplicate calls and
  duplicate outputs are rejected rather than hidden by removal
- **AND** valid search history leaves portable dialogue unchanged; an unmatched
  call alone does not invent an output or change the existing pending-call policy.

#### Scenario: A running tool delivers later results

- **WHEN** a function or custom-tool call already has its initial result and
  later output items have distinct nonempty item IDs, the same call kind and
  `call_id`, and a nonempty tool name matching the original call
- **THEN** projection keeps the original pair and emits each later result as
  an explicitly attributed `tool_delivery` assistant commentary message at its
  original position, retaining the visible text, call identity and tool name
- **AND** projection, diagnosis and recovery use the same relationship decision;
  a recovery suffix cannot retain a delivery after removing its original call
- **AND** no result replaces an earlier result, no synthetic call is invented,
  and repeated projection is byte-stable. This named textual delivery contract
  does not imply support for every native asynchronous API feature.

### Requirement: Provider extension is adapter-only

A new upstream Provider SHALL be admitted through one manifest entry and, only
where its wire differs, one policy module that the entry declares. Generic HTTP
admission, Responses semantics, lifecycle, CLI, and client configuration SHALL
NOT branch on Provider names.

#### Scenario: A Provider is added

- **WHEN** its endpoint and any wire policy are declared in the manifest
- **THEN** its route serves through the existing Responses path, and a policy
  that is missing, incomplete or outside its declared package fails the load
- **AND** no existing generic product module requires Provider-specific code.

### Requirement: HTTP request framing precedes Responses interpretation

Responses admission SHALL read exactly one body framed by a single ASCII
decimal `Content-Length`. Unsupported transfer encodings, absent, ambiguous or
invalid lengths, and incomplete bodies SHALL receive one secret-free local
failure before projection or upstream I/O. Rejection SHALL close the connection
so unread bytes cannot become another request.

#### Scenario: A caller supplies unproved framing

- **WHEN** a Responses request supplies an unsupported transfer encoding or
  missing, repeated, nondecimal, negative or unrepresentable body length
- **THEN** it receives HTTP 400 with `request_framing_rejected` and a fixed reason
- **AND** no header value, request content, traceback or upstream request is emitted.

#### Scenario: The caller closes its write side before the declared body ends

- **WHEN** the available bytes form valid JSON but are fewer than the declared length
- **THEN** HTTP 400 identifies `incomplete_request_body` without forwarding that prefix
- **AND** the connection closes rather than interpreting another request.

#### Scenario: A complete request is followed by another keep-alive request

- **WHEN** the caller supplies one complete length-framed Responses body
- **THEN** projection consumes only that body and computes the upstream length
- **AND** a subsequent complete request remains independently parseable.

### Requirement: Responses body admission has finite byte and time budgets

Responses admission SHALL reject a declared body above its configured byte
limit before reading it. Every body read SHALL spend one monotonic deadline;
partial progress SHALL NOT renew it. Over-limit, expired and unreadable bodies
SHALL receive one secret-free local failure before projection or upstream I/O,
close the connection and leave other requests usable. Native runtime settings
SHALL own and validate these budgets independently of upstream inference.

#### Scenario: A body length exceeds the selected limit

- **WHEN** a single valid decimal length exceeds the body limit, even if it
  exceeds the platform's native read-size range
- **THEN** HTTP 413 reports `request_body_rejected` and `request_body_too_large`
- **AND** no body read, projection, upstream request or second HTTP reply occurs.

#### Scenario: The caller stalls or continually sends small body fragments

- **WHEN** the body cannot finish within its original monotonic deadline
- **THEN** HTTP 408 reports `request_body_rejected` and `request_body_timeout`
- **AND** no partial body, credential or traceback appears in the error or log;
  the original socket timeout is restored before the connection closes.

#### Scenario: A complete body fits the limit and its deadline

- **WHEN** the caller sends exactly the admitted byte limit before expiry
- **THEN** only those bytes enter portable projection, unchanged when already portable
- **AND** a subsequent keep-alive request remains parseable; the body deadline
  does not shorten inference, streamed output or the next request.

### Requirement: Catalog exchanges have independent finite budgets

Each model catalog GET SHALL use one validated deadline and byte limit,
independent of Responses inference. Success and HTTP errors SHALL forward
only admitted bytes, once, without replay or recovery. Headers, body reads
and downstream writes SHALL share the original deadline. Failure after
commitment SHALL close without another status or successful terminator;
all paths SHALL release upstream ownership and restore the socket timeout.

#### Scenario: Catalog success or HTTP error exceeds its budget

- **WHEN** catalog headers expire before commitment
- **THEN** one bounded local HTTP 504 reports `catalog_deadline_exceeded`
- **AND** a local failure closes its connection and gets at most one separate
  configured catalog timeout for delivery; an unavailable client cannot hold it
- **AND** a body exceeding time or bytes after commitment preserves only its
  admitted prefix and closes, without retry, changed status or completion
- **AND** native HTTP framing progress cannot renew the deadline. System DNS
  cancellation and inbound header deadlines remain separately unproved.

#### Scenario: A catalog fits its exact byte limit

- **WHEN** a success or HTTP error completes within the selected limits
- **THEN** its status, end-to-end headers and exact body are relayed once
- **AND** normal HTTP completion remains distinguishable from truncation;
  catalog failures do not enter Responses admission, cooldown or recovery.

### Requirement: Sustained upstream unavailability does not amplify demand

Responses SHALL retain bounded request-local recovery for a transient upstream
`503`. When that recovery ends in `503`, the proxy SHALL relay the upstream
status and body and remember a bounded cooldown for that Provider only.
During the cooldown, another Responses request for that Provider SHALL receive
a typed local `503` with `Retry-After` before upstream I/O. The cooldown SHALL
not block another Provider or non-Responses resource, and a request after expiry
SHALL probe the upstream again.

#### Scenario: A Provider remains unavailable across turns

- **WHEN** one Responses turn exhausts its bounded retries with upstream `503`
- **THEN** that turn receives the original terminal `503`
- **AND** an immediate subsequent turn for the same Provider receives a local
  `provider_unavailable_cooldown` response without upstream I/O
- **AND** another Provider remains usable; after expiry the first Provider is
  probed again.

### Requirement: Upstream redirects reach the client unfollowed

The relay SHALL NOT follow an upstream redirect. A redirect response from the
selected upstream SHALL reach the client as that response, and the client's
request, including its credential, SHALL be sent only to the selected route's
upstream, never to the URL that the redirect names.

#### Scenario: An upstream redirects to another host

- **WHEN** an upstream answers a Responses or model-catalog request with a
  redirect to another host or scheme
- **THEN** the proxy sends no request to the redirect target
- **AND** the client receives the upstream's redirect status and `Location`.

### Requirement: HTTP event-stream commitment and release are irreversible

Each HTTP Responses stream SHALL forward validated available events without
waiting to fill an application buffer or for upstream EOF. A terminal
`response.completed`, `response.incomplete`, or `response.failed` event SHALL
finish that response's stream; subsequent bytes SHALL NOT reopen or extend it.
This single-response HTTP contract SHALL NOT be applied to a persistent
WebSocket that can carry successive responses and steering events.

#### Scenario: A complete event arrives on a held-open chunked connection

- **WHEN** the upstream sends a terminal event but leaves its HTTP connection open
- **THEN** the client receives that complete response without waiting for EOF
- **AND** the upstream attempt is released without another request.

### Requirement: Upstream connection release and failure commitment are bounded

Every upstream attempt SHALL release its connection after success, rejection,
exception, downstream disconnect or a downstream write that misses the exchange
deadline. Before downstream commitment, a malformed event SHALL yield one
structured HTTP failure.

#### Scenario: Retries and reads share the original exchange deadline

- **WHEN** one Responses exchange acquires headers, reads an HTTP error or JSON
  body, waits for classified recovery, or reconnects an uncommitted SSE stream
- **THEN** every operation consumes the same monotonic deadline created for
  that exchange; a new attempt or body phase cannot reset its budget
- **AND** an expired pre-commit exchange returns one structured failure without
  starting another request; an expired committed stream closes without replay,
  a second status line or a fabricated successful terminator
- **AND** buffered JSON and error bodies remain byte-bounded and owned upstream
  connections are released on success, rejection and read failure.

#### Scenario: HTTP framing arrives one small fragment at a time

- **WHEN** the upstream continually supplies partial headers, chunk sizes or
  trailers within each idle socket timeout
- **THEN** every raw HTTP read is clamped to the original exchange deadline;
  framing progress cannot renew the exchange or admit a late stream completion
- **AND** HTTP and certificate-verified HTTPS use the native parser without a
  timer thread, alternate service or a patched standard library

#### Scenario: Pending SSE data exceeds its byte budget

- **WHEN** one framed or partial event, or the held pre-content prelude, exceeds
  the persisted SSE byte budget (64 MiB by default)
- **THEN** the attempt stops without reconnecting and releases its connection;
  an uncommitted exchange returns one `stream_buffer_limit` error, while a
  committed exchange preserves emitted bytes and closes without success
- **AND** bounded events keep their order and exact bytes, including ciphertext;
  total stream output is not capped, and fragmented input is extended in place
  with framing searches limited to the newly extended suffix.

#### Scenario: Connection setup and request writes spend the original budget

- **WHEN** native DNS returns addresses, TCP falls back to another address, TLS
  handshakes, or the request sends headers and body
- **THEN** each blocking socket phase uses only the remaining monotonic budget
- **AND** an expired resolution starts no socket or request, failed address
  sockets close, and TLS retains the selected trust store and hostname checking
- **AND** the exchange waits for native address resolution only within that
  budget; at most four resolver threads run lookups, exchanges for one address
  share a lookup, and a lookup that outlives its waiters holds no exchange,
  upstream connection or admission slot.

#### Scenario: A client stops reading the response

- **WHEN** the downstream client stops reading while the proxy writes the
  status, headers, events or terminator of one exchange
- **THEN** each write uses only the remaining budget; at the deadline the proxy
  closes both connections and releases the admission slot
- **AND** a structured failure that starts after the deadline has one short
  write budget of one second instead of an unbounded wait.

#### Scenario: Sequential exchanges reach the same upstream address

- **WHEN** an upstream response was read to the end without an announced close
  and a later exchange targets the same scheme, host and port
- **THEN** the later exchange reuses that idle connection only while it is at
  most 30 seconds old and its peer has not closed it, sends every header that
  urllib builds except the forced `Connection: close`, and spends only its own
  budget
- **AND** at most eight idle connections are kept per address, and a partly
  read, abandoned or failed response closes its connection

#### Scenario: The upstream may have accepted a request whose response was lost

- **WHEN** the connection fails after the complete request was sent, or an
  uncommitted stream ends before content without `response.failed`
- **THEN** the proxy returns one `502 upstream_outcome_unknown` and does not
  send that request again; after a connection failure, only a request that was
  not sent completely is retried.

#### Scenario: Output is malformed after downstream commitment

- **WHEN** output is malformed or unterminated after downstream commitment
- **THEN** the existing response closes without a second status line, a successful
  chunk terminator, fabricated completion or request replay
- **AND** only the existing classified pre-content recovery may reopen a request.

#### Scenario: The client disconnects during a write

- **WHEN** a downstream header or body write raises an exception
- **THEN** the owned upstream response is closed exactly once
- **AND** no retry starts for the disconnected client.

#### Scenario: A malformed event follows visible content

- **WHEN** HTTP 200 and validated content have already reached the client
- **THEN** the client observes a truncated response, not a second HTTP 503
- **AND** the proxy records the failure and releases the upstream connection.

### Requirement: Plaintext agent delivery is selected before generation

A released provider profile SHALL select native or plaintext agent message
delivery. Omission SHALL select native delivery. The plaintext policy SHALL
bind one request's schema, replay and response restoration without accessing
client configuration, credentials or conversation storage.

#### Scenario: A route selects readable agent messages

- **WHEN** a plaintext route receives the native `collaboration` catalog
- **THEN** it aliases that catalog and explicit tool choice upstream and removes
  encryption annotations only from spawn, message and followup message schemas
- **AND** JSON and streaming responses restore the native namespace and, for
  function calls, the empty encrypted-argument marker only after complete
  readable arguments are proved
- **AND** exact message arguments and call identities survive sender-to-recipient
  delivery using the client's native plaintext path.

#### Scenario: A selected custom call preserves its native argument field

- **WHEN** JSON or streaming output contains a selected custom message call
- **THEN** the declared call policy selects `input`, not function `arguments`,
  for readable-message validation and preserves its exact value and call identity
- **AND** no function-only encrypted-argument marker is added or accepted;
  replay retains the custom call and output grammar.

#### Scenario: A terminal delivery retains an in-progress item status

- **WHEN** a terminal Response or `response.output_item.done` contains a selected
  message call still marked `in_progress` with no readable arguments
- **THEN** the terminal context requires complete readable arguments and fails
  locally without restoring or dispatching that call
- **AND** an initial `response.output_item.added` may precede argument generation
  without an empty encrypted-argument marker; any previously emitted bytes remain
  unchanged and the committed request is not reopened.

#### Scenario: Old ciphertext or conflicting metadata is present

- **WHEN** retained history contains encrypted native agent content, or a
  selected output carries ciphertext or nonempty encrypted argument metadata
- **THEN** retained history preserves its exact envelope and ciphertext
- **AND** output cannot be relabeled as plaintext or retried with its message
  removed; unproved selected output fails locally.

#### Scenario: Live validation fails after downstream content

- **WHEN** malformed SSE data or unproved selected agent arguments follow
  downstream content
- **THEN** the relay preserves emitted bytes, closes the incomplete connection
  and does not reopen the committed request
- **AND** selected call types and names are strings before semantic selection;
  a malformed selected value fails with a bounded delivery reason, while an
  unrelated namespace retains its exact bytes
- **AND** operational evidence identifies the validator-owned fixed reason
  without message arguments, ciphertext, query values or exception prose
- **AND** an unclassified validation exception remains `unknown`.

#### Scenario: The client continues an admitted agent conversation

- **WHEN** the client's own configuration aligns its native namespace and model
  agent version with the conversation
- **THEN** acceptance proves real child tool execution, bidirectional explicit
  messages, automatic completion and followup tasks on the selected route
- **AND** native resume and compaction retain exact content witnesses
- **AND** source acceptance alone does not claim release, installed runtime,
  original-chat recovery or readability of earlier ciphertext.
