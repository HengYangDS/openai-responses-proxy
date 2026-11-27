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
upstream rejection SHALL NOT substitute a header-only task. The projected form
SHALL be sent on every route except where a route forwards a stateless request
unchanged, as "A route may forward stateless requests unchanged" states; a
request that the projection rejects SHALL be rejected on every route.

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

## ADDED Requirements

### Requirement: A route may forward stateless requests unchanged

A provider route MAY declare in the release-owned manifest that it forwards
stateless requests unchanged. On such a route, a request that the projection
admits, whose `store` is `false` and that has no `previous_response_id` or
`conversation`, SHALL reach the upstream as the client's exact bytes. Any other
request SHALL take the projected form. A route SHALL NOT declare this together
with plaintext agent delivery.

#### Scenario: Codex continues a conversation on a pass-through route

- **WHEN** Codex sends a request with `store=false`, replayed reasoning items,
  message identifiers, `prompt_cache_key` and `include` to a route that
  declares pass-through
- **THEN** the upstream receives exactly the bytes that Codex sent.

#### Scenario: A request asks the upstream to keep state

- **WHEN** a request to a pass-through route omits `store=false` or names
  `previous_response_id` or `conversation`
- **THEN** the upstream receives the projected form.

#### Scenario: An unproved request reaches a pass-through route

- **WHEN** a request that the projection rejects is sent to a pass-through route
- **THEN** the proxy rejects it locally and performs no remote request.

#### Scenario: The manifest combines pass-through with plaintext delivery

- **WHEN** a manifest entry declares pass-through and plaintext agent delivery
- **THEN** the manifest fails to load.

### Requirement: An unchanged request falls back to its projection

When the upstream answers an unchanged request with HTTP 400, type
`invalid_request_error` and code `invalid_encrypted_content`, the proxy SHALL
send the projected form of that request once. Every other request-changing
recovery of an unchanged request SHALL derive from its projected form.

#### Scenario: The upstream refuses the unchanged request's state

- **WHEN** the upstream answers an unchanged request with HTTP 400 and code
  `invalid_encrypted_content`
- **THEN** the proxy sends the projected form once
- **AND** a second refusal reaches the client unchanged.

#### Scenario: An unchanged request needs a smaller input

- **WHEN** the upstream refuses an unchanged request for its input size
- **THEN** the smaller request that the proxy sends derives from the projected
  form, not from the client's bytes.
