## ADDED Requirements

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

#### Scenario: An ordinary Responses message omits its optional type

- **WHEN** a standard input message supplies its role and content without the
  optional `type` discriminator
- **THEN** admission projects it through the same message owner as an explicit
  `type: message`, preserving role, content order and supported phase
- **AND** invalid roles, content and unknown fields retain their declared
  rejection; a present invalid discriminator is not treated as omission.

#### Scenario: Replay contains an inline screenshot

- **WHEN** image-capable input contains an HTTP(S) image URL or a nonempty,
  strictly Base64-encoded PNG, JPEG, WebP, or GIF data URL
- **THEN** the shared content projection preserves the original URL, detail,
  content order, and image-only message or paired tool output
- **AND** repeated projection and classified retries retain those image bytes without increasing
  the omitted-image count
- **AND** transport validation performs no filesystem access, network image
  fetch, raster decoding, or image re-encoding. Local file references and
  malformed image transports retain their existing disposition.

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

A new upstream Provider SHALL be admitted through one manifest entry, one narrow
wire adapter, one policy declaration, and the common conformance suite. Generic
HTTP admission, Responses semantics, lifecycle, CLI, and client configuration
SHALL NOT branch on Provider names.

#### Scenario: A Provider is added

- **WHEN** its endpoint and wire differences are declared
- **THEN** the common conformance suite proves request, stream, non-stream,
  error, retry, cooldown, and redaction behavior
- **AND** no existing generic product module requires Provider-specific code.

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
exception or downstream disconnect. Before downstream commitment, a malformed
event SHALL yield one structured HTTP failure.

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

#### Scenario: Connection setup and request writes spend the original budget

- **WHEN** native DNS returns addresses, TCP falls back to another address, TLS
  handshakes, or the request sends headers and body
- **THEN** each blocking socket phase uses only the remaining monotonic budget
- **AND** an expired resolution starts no socket or request, failed address
  sockets close, and TLS retains the selected trust store and hostname checking
- **AND** system DNS remains synchronous and is not cancellable by a socket
  timeout; its wall-clock cancellation is unproved, not hidden by a worker or
  alternate resolver.

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
- **AND** JSON and streaming responses restore the native namespace and empty
  encrypted-argument marker only after complete readable arguments are proved
- **AND** exact message arguments and call identities survive sender-to-recipient
  delivery using the client's native plaintext path.

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

## MODIFIED Requirements

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

- **WHEN** a subsequent request switches among UCloud, DMXAPI, and AIHubMix
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
