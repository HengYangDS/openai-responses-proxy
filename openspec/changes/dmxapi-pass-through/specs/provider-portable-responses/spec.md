## RENAMED Requirements

- FROM: `### Requirement: Every Responses request is projected to a provider-portable form`
- TO: `### Requirement: Every Responses request is admitted through the provider-portable projection`

## MODIFIED Requirements

### Requirement: Every Responses request is admitted through the provider-portable projection

Before upstream I/O, the proxy SHALL project the current request through one
proved portability grammar, preserving portable input and generation settings.
It SHALL remove provider-bound continuation and optional encrypted replay state,
set `store=false`, and omit requests for `reasoning.encrypted_content`. Required
encrypted agent tasks SHALL keep their native envelope and exact ciphertext;
upstream rejection SHALL NOT substitute a header-only task. Every attempt SHALL
send this projected request, except the first attempt of a stateless request on
a route that forwards it unchanged; a request that the projection rejects SHALL
be rejected on every route.

#### Scenario: A stored conversation changes provider

- **WHEN** a request to a portable route contains `previous_response_id`,
  `conversation`, `prompt_cache_key`, stored-item references, replayed
  reasoning items such as `rs_*`, optional encrypted output, or another
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
- **AND** no optional reasoning or continuation binding crosses into a portable
  route
- **AND** portable user and plaintext agent content remains replayable; encrypted
  delegation additionally requires an upstream capable of interpreting it.

### Requirement: Portable dialogue and tool relationships are preserved

The proxy SHALL preserve role-valid dialogue, agent author/recipient/phase,
complete function/custom-tool call-output pairs, and standalone deliveries with
explicit portable provenance. In a projected request, assistant, plaintext
synthesized-agent, and standalone history SHALL use Easy Input Message strings; instructions, user
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

- **WHEN** an encrypted agent task in a projected request receives HTTP 400
  with `type=invalid_request_error` and `code=invalid_encrypted_content`
- **THEN** the proxy relays that rejection without a further upstream request
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
plaintext empty-result marker in the projected request. The exception SHALL
NOT apply to ordinary dialogue, missing or null output, or an invalid pair. A
Codex-generated assistant placeholder consisting of exactly one valid, empty
`output_text` block SHALL be treated as non-semantic history and omitted rather
than forwarded or rejected.

#### Scenario: A paired tool result is textually empty

- **WHEN** a valid function or custom-tool output follows its matching call and
  its exact result is the empty string
- **THEN** the projected request retains the pair with the fixed empty-result
  marker
- **AND** it is not rejected as an empty dialogue message.

#### Scenario: A replay contains an empty assistant placeholder

- **WHEN** Codex replay contains an assistant message whose content is exactly
  one valid `output_text` block with an empty string
- **THEN** the projected request omits that non-semantic item
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
- **THEN** the projected request omits the complete local shell pair
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

## ADDED Requirements

### Requirement: A route may forward stateless requests unchanged

A provider route MAY declare in the release-owned manifest that it forwards
stateless requests unchanged. On such a route, the first attempt of a request
that the projection admits, whose `store` is `false` and that has no
`previous_response_id`, `conversation` or item reference, SHALL carry the
client's exact bytes, and every later attempt SHALL send the projected request.
A route SHALL NOT declare this together with plaintext agent delivery.

#### Scenario: Codex continues a conversation on a pass-through route

- **WHEN** Codex sends a request with `store=false`, replayed reasoning items,
  message identifiers, `prompt_cache_key` and `include` to a route that
  declares pass-through
- **THEN** the upstream receives exactly the bytes that Codex sent.

#### Scenario: A request asks the upstream to keep state

- **WHEN** a request to a pass-through route omits `store=false`, or names
  `previous_response_id`, `conversation` or an item reference
- **THEN** the upstream receives the projected request.

#### Scenario: An unproved request reaches a pass-through route

- **WHEN** a request that the projection rejects is sent to a pass-through route
- **THEN** the proxy rejects it locally and performs no remote request.

#### Scenario: The client's bytes are sent at most once

- **WHEN** the first attempt of a stateless request ends in a transient error,
  an empty-response retry or a stream failure before content
- **THEN** the next attempt sends the projected request.

#### Scenario: A route combines pass-through with plaintext delivery

- **WHEN** a provider route declares pass-through and plaintext agent delivery
- **THEN** the route cannot be created and the manifest fails to load.

### Requirement: A refused unchanged request is sent in its projected form

When the upstream answers the client's unchanged bytes with HTTP 400, the proxy
SHALL send the projected request once before any other recovery. A refusal of
the projected request SHALL then follow the recovery of a portable route.

#### Scenario: The upstream refuses the unchanged request's state

- **WHEN** the upstream answers an unchanged request with HTTP 400 and code
  `invalid_encrypted_content`
- **THEN** the proxy sends the projected request once
- **AND** a second refusal reaches the client unchanged.

#### Scenario: The upstream refuses the unchanged request for another reason

- **WHEN** the upstream answers an unchanged request with any other HTTP 400
- **THEN** the proxy sends the projected request once
- **AND** a refusal of it for its input size leads to a smaller request derived
  from the projected request, not from the client's bytes.
