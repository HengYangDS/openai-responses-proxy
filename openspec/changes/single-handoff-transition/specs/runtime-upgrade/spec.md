## ADDED Requirements

### Requirement: A lifecycle transition hands off or is refused

Upgrade, rollback and reload SHALL replace a running listener only through the
shared-listener handoff. A runtime qualifies when its identity is complete and
it declares both the `selected-generation-handoff` and the
`admission-preserving-handoff` capability. Any other runtime SHALL be refused
before a projection, a service definition or a process changes.

#### Scenario: A runtime declares both handoff capabilities

- **WHEN** a verified runtime declares both capabilities
- **THEN** upgrade, rollback and reload use the shared-listener handoff
- **AND** successor acceptance still requires the exact payload and process
  identity.

#### Scenario: A runtime lacks a handoff capability

- **WHEN** a verified runtime declares one of the capabilities or neither
- **THEN** upgrade and rollback fail with the incompatible-runtime result,
  which names removal before installation, and reload fails
- **AND** no payload projection, service definition or process changes, and no
  transaction remains.

#### Scenario: Runtime identity is incomplete

- **WHEN** listener, payload, protocol, transaction, or process identity is
  absent or inconsistent
- **THEN** no handoff is attempted
- **AND** the operator receives one precise incompatibility result.

#### Scenario: A handoff fails before its successor commits

- **WHEN** an upgrade or rollback handoff fails before its successor commits
- **THEN** the payload is rolled back only after health proves that the
  captured predecessor still owns the listener, admits requests and has begun
  no handoff since its capture
- **AND** without that proof the transaction is preserved for recovery as an
  unknown outcome
- **AND** the controller sends the predecessor no request that changes its
  admission.

#### Scenario: A listener refuses a handoff request before beginning it

- **WHEN** a listener answers a handoff request without beginning the handoff
- **THEN** the controller reads that the listener began nothing and rolls the
  payload back without waiting out the handoff deadline
- **AND** this holds for a listener that an earlier handoff created, whose
  health still shows that finished handoff.

### Requirement: The listener exposes no admission drain

The listener SHALL NOT offer a control route that closes or reopens Responses
admission, and SHALL NOT refuse a request because a lifecycle transition is in
progress. Health SHALL report `accepting` as true and `draining` as false,
which handoff protocol 2 names, and SHALL carry no drain generation or drain
lease.

#### Scenario: A client requests the former drain route

- **WHEN** a client sends `POST` or `DELETE` to `/control/drain`
- **THEN** the listener answers as it does for any path that names no provider
  route
- **AND** a later Responses request is admitted.

#### Scenario: Health is read while the listener serves

- **WHEN** a controller reads health
- **THEN** `accepting` is true and `draining` is false
- **AND** no drain generation or drain lease field is present.

### Requirement: A refused rollback leaves no transaction

A rollback that is refused before any projection SHALL close the transaction
that it opened, so that the next command needs no recovery.

#### Scenario: The running runtime cannot hand off

- **WHEN** `rollback` opens its transaction and the running runtime is then
  refused
- **THEN** the journal is absent after the failure
- **AND** the selector and the installed record are unchanged.

## MODIFIED Requirements

### Requirement: Rollback owns only current product files

Rollback SHALL snapshot the complete current owned inventory or its complete
absence.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Unknown install content SHALL be preserved and SHALL never become
  implicitly owned. Candidate paths that collide with unknown content SHALL block
  mutation. When an upgrade fails after projecting candidate bytes, rollback
  SHALL restore every retained prior byte and remove every verified candidate
  file that was absent from the prior snapshot. Explicit rollback to the selected
  predecessor SHALL use the same shared-listener handoff as an upgrade.

#### Scenario: A retained predecessor predates the current handoff capability

- **WHEN** an operator rolls back and the retained predecessor cannot complete
  the handoff as the successor
- **THEN** the current listener keeps serving and the reverse transaction is
  rolled back
- **AND** rollback reports the failure without draining the listener or
  replacing its process.

#### Scenario: Current payload upgrade fails

- **WHEN** candidate commit or successor proof fails
- **THEN** every prior owned bundle byte and mode is restored
- **AND** unknown content remains unchanged.

#### Scenario: Candidate adds a new frozen-runtime member

- **WHEN** an upgrade projects a verified candidate-only file below `bin/`
- **AND** handoff fails and rollback runs
- **THEN** the candidate-only file is removed
- **AND** every prior owned byte and mode is restored exactly
- **AND** content outside prior-owned and candidate inventories is unchanged.

#### Scenario: Candidate collides with unknown content

- **WHEN** a candidate path already contains content outside the current owned inventory
- **THEN** the upgrade blocks before payload mutation
- **AND** rollback never claims ownership of that content.

#### Scenario: Retired payload paths leave empty descendants

- **WHEN** an upgrade removes files owned only by the verified predecessor
- **THEN** it also removes empty directories below those retired file parents
- **AND** it preserves every unowned file and symbolic link
- **AND** rollback restores predecessor-owned bytes without recreating
  semantically empty directory residue.

## REMOVED Requirements

### Requirement: Repeated lifecycle transitions use declared runtime capability

**Reason**: It let a runtime without the handoff capability be drained and replaced, a transition that the product no longer has.

**Migration**: "A lifecycle transition hands off or is refused" states the one remaining transition and its refusal.
