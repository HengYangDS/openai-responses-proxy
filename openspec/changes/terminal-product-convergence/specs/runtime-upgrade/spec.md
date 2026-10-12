## ADDED Requirements

### Requirement: Supported native lifecycles are behaviorally symmetric

macOS, Linux, and Windows SHALL expose the same public install, status, doctor,
reload, rollback, recover, upgrade, and uninstall semantics while native adapters
own only their operating-system service operations. Each platform SHALL prove
the complete lifecycle using its own released artifact; a container, mock,
source execution, cross-compilation, or another operating system SHALL NOT
substitute for native product evidence.

#### Scenario: A supported platform accepts a release

- **WHEN** its signed native artifact is installed into an isolated user context
- **THEN** install, health, active-target no-op, upgrade, rollback, recovery,
  re-upgrade, uninstall, and reinstall reach their declared terminal states
- **AND** teardown leaves no owned service, process, transaction, payload,
  command, temporary carrier, or host-configuration residue.

#### Scenario: The requested artifact is already active

- **WHEN** a signed artifact exactly matches the healthy active release receipt
  and serving payload, with an idle transaction journal and verified supervisor
- **THEN** `install` returns `unchanged` without creating a transaction, replacing
  files, rebinding supervision, or restarting the listener
- **AND** the lifecycle operation uses the same context that owns its mutation
  lock rather than reconstructing paths or authority during installation.

#### Scenario: Linux service admission fails without a user manager

- **WHEN** Linux installation cannot reach a systemd user manager before any
  service registration or process creation
- **THEN** it reports `native_service_unavailable` and restores payload,
  command, and transaction state without invoking native-service removal
- **AND** the error describes the required user-service environment rather
  than a deleted generation path or private process entrypoint
- **AND** an unreachable manager remains an unknown service observation, not
  proof of absence; uncertain failures after service mutation still preserve
  the transaction for recovery.

#### Scenario: Linux installation preserves user-wide host policy

- **WHEN** a reachable user manager accepts the Proxy service
- **THEN** installation changes only its owned unit and process resources
- **AND** user lingering remains host-administered policy, not an implicit
  installer side effect or a source of credential prompts.

### Requirement: Terminal cleanup survives process interruption

Once a transaction has completed its projection and required supervisor binding,
its terminal outcome SHALL survive partial removal of transaction-owned
resources. Recovery SHALL continue disposal without repeating those completed
effects or depending on a payload or snapshot already removed. Transaction
authority SHALL remain available until disposal completes, and a new transaction
SHALL wait for that completion.

#### Scenario: Cleanup is interrupted after terminal effects

- **WHEN** cleanup fails after removing part of a discarded generation, a
  rollback snapshot, or the entire temporary transaction directory
- **THEN** a new process completes only the remaining owned cleanup
- **AND** selected payloads, installed metadata, command projection, and native
  supervision remain unchanged.

#### Scenario: Payload removal is interrupted

- **WHEN** native supervision and owned processes have stopped and a purge
  removes only part of its verified payload
- **THEN** the existing transaction journal retains the exact root-relative
  file digests until disposal completes
- **AND** either `recover` or repeated `uninstall --purge` resumes that removal
  without requiring a deleted executable, manifest, or generation selector
- **AND** changed files, symbolic links, unknown files, and unknown empty
  directories remain untouched and prevent a successful purge result.

#### Scenario: An interrupted first installation has a starting native service

- **WHEN** a fresh installation committed a candidate and submitted native
  supervision, but recovery cannot prove an accepting runtime
- **THEN** recovery removes only the journal-bound candidate's exact native
  registration and owned processes before discarding payload or recovery
  authority
- **AND** an absent health response does not establish service absence
- **AND** selector, rollback snapshot, and command restoration are admitted
  before native disposal; any drift preserves supervision and recovery input
- **AND** a missing configured executable permits cleanup only after proved
  native absence
- **AND** unknown cleanup or mismatched supervision preserves the journal and
  candidate without a successful rollback result.

#### Scenario: Windows cannot observe the exact scheduled task

- **WHEN** the exact local task query fails, times out, or returns malformed task
  XML
- **THEN** native status and recovery preserve unknown service state and refuse
  native disposal
- **AND** only `HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)` or
  `HRESULT_FROM_WIN32(ERROR_PATH_NOT_FOUND)` from that query establishes task
  absence, independent of the display language
- **AND** running classification uses owned watchdog processes, not localized
  status text.

### Requirement: Capable handoff preserves request admission

A runtime-to-runtime handoff that advertises the admission-preserving capability
SHALL transfer listener ownership without changing the request-admission state.
Draining is reserved for the legacy native-generation replacement path whose
current runtime cannot perform that handoff.

#### Scenario: A capable runtime is upgraded or rolled back under load

- **WHEN** a verified current runtime advertises both selected-generation and
  admission-preserving handoff capabilities
- **THEN** upgrade or rollback uses that handoff while new requests continue to
  be admitted and already accepted requests reach terminal responses
- **AND** no request is rejected with `proxy_draining` during the transition.

#### Scenario: A legacy runtime cannot preserve admission

- **WHEN** the verified current runtime lacks the admission-preserving handoff
  capability
- **THEN** lifecycle selects the bounded native-generation replacement path
- **AND** health and command output expose the actual admission state without
  claiming an admission-preserving transition.

### Requirement: Legacy lifecycle state has no implicit compatibility authority

An installed payload, journal, launcher, supervisor, manifest, command, or
schema shape that cannot satisfy the current exact ownership and transition
contract SHALL be rejected before mutation. After the terminal lifecycle has
no consumer for an earlier shape, its reader, writer, fallback, migration
bypass, and tests SHALL be deleted.

#### Scenario: A legacy carrier is encountered

- **WHEN** current code cannot prove its exact ownership and safe transition
- **THEN** the public command reports the bounded removal or reinstall action
- **AND** no compatibility inference or permissive fallback mutates it.

#### Scenario: An installed payload has no generation selector

- **WHEN** an upgrade encounters installed metadata without a durable generation
  selector
- **THEN** it requests explicit uninstall and fresh installation before creating
  a transaction or changing payload, command, or service state
- **AND** fresh installation creates only its command rollback record, while
  current-layout upgrade and rollback reuse the selected immutable generations
  without a parallel payload snapshot or layout migration.
