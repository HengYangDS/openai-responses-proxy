## MODIFIED Requirements

### Requirement: The installed payload has one current shape

The installed payload SHALL contain one prewarmed native bundle under `bin/`,
its complete manifest, signed-release receipt, and finalized
state.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Installation SHALL accept only an empty target or one verified current
  native listener whose native supervisor declares the canonical installed
  executable. After candidate projection, installation SHALL remove only regular
  files declared by the verified predecessor manifest and absent from the
  successor manifest. Unknown installation content SHALL remain untouched, and
  rollback SHALL restore the complete predecessor projection.

#### Scenario: An incompatible installation is present

- **WHEN** its manifest, bundle inventory, executable, listener identity, or
  canonical supervisor declaration does not match the admitted shape
- **THEN** installation fails before mutation with one bounded removal action
- **AND** no legacy inventory reader, one-file entrypoint, interpreter
  entrypoint, external launcher, or bypass switch admits it.

#### Scenario: A candidate bundle is admitted

- **WHEN** signed release verification has produced the complete candidate
  inventory
- **THEN** every regular file and mode is verified in staging
- **AND** the staged executable completes a bounded prewarm probe
- **AND** native supervision already declares the canonical installed
  executable
- **AND** only then may the payload transaction replace installed bytes.

#### Scenario: The successor omits a predecessor-owned file

- **WHEN** the verified predecessor manifest owns a regular file that the
  verified successor manifest does not declare
- **THEN** installation removes that file after writing the successor
  projection
- **AND** preserves every unowned file
- **AND** rollback restores the removed predecessor-owned file exactly.

### Requirement: The listener exposes no admission drain

The listener SHALL NOT offer a control route that closes or reopens Responses
admission, and SHALL NOT refuse a request because a lifecycle transition is in
progress. Health SHALL report `accepting` as true and `draining` as false,
which handoff protocol 2 names, and SHALL carry no drain generation or drain
lease.

#### Scenario: A client requests the former drain route

- **WHEN** a client sends `POST` or `DELETE` to `/control/drain`
- **THEN** the listener answers as it does for any path that names no upstream
- **AND** a later Responses request is admitted.

#### Scenario: Health is read while the listener serves

- **WHEN** a controller reads health
- **THEN** `accepting` is true and `draining` is false
- **AND** no drain generation or drain lease field is present.
