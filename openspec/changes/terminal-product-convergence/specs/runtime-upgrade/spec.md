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

### Requirement: Reload starts a stopped installation through native supervision

When no runtime is live and status classifies the installation as `stopped`,
`reload` SHALL bind the native supervisor to the lifecycle control executable
through the adapter's installation operation and SHALL report `started` only
after the selected payload's listener proves SERVING identity within the
operator deadline. Every supported platform SHALL use this one path.

#### Scenario: The supervisor declaration survived a restart

- **WHEN** a `stopped` installation is reloaded on macOS, Linux, or Windows
- **THEN** native supervision is bound and its configured executable is proved
  to be the lifecycle control executable
- **AND** one accepting, non-draining listener proves the selected payload's
  release, manifest, serving aggregate, and receipt before `started` is reported
- **AND** no payload, selector, installed record, command or transaction
  changes.

#### Scenario: The started listener cannot be proved

- **WHEN** supervision binds but no serving listener is proved within the
  deadline
- **THEN** reload fails with one bounded lifecycle error
- **AND** the bound supervisor remains in place because no predecessor runtime
  exists to restore.

### Requirement: Reload rebinds native supervision after a handoff

`reload` SHALL hand off a serving installation only while its owned native
supervisor declaration binds the lifecycle control executable, and SHALL then
bind native supervision to that executable through the adapter's installation
operation before reporting `reloaded`. Every supported platform SHALL use this
one path.

#### Scenario: A serving installation lost its native supervisor

- **WHEN** a verified listener serves while its owned supervisor declaration
  binds the lifecycle control executable but the supervisor does not run
- **THEN** `reload` hands off to a successor that proves SERVING identity and
  then binds native supervision
- **AND** `status` afterwards reports `running` with a running native service.

#### Scenario: The declaration does not bind lifecycle control

- **WHEN** the owned supervisor declaration is absent or names another
  executable
- **THEN** `reload` refuses before any handoff or supervision change
- **AND** it names read-only `status --json`.

#### Scenario: Rebinding fails after the handoff

- **WHEN** binding native supervision fails after the successor proved SERVING
  identity
- **THEN** `reload` fails with one bounded lifecycle error that names the
  serving successor
- **AND** the handoff is not reversed.

### Requirement: Installing the installed artifact restores native supervision

When a signed artifact exactly matches a verified, transaction-free
installation whose native supervision is absent or declared but not running,
`install` SHALL restore the command projection, bind native supervision to the
lifecycle control executable, and report `installed` only after a listener
proves the artifact's SERVING identity. It SHALL change no payload file,
selector or installed record.

#### Scenario: Uninstall preserved the verified payload

- **WHEN** `uninstall` removed native supervision and the command while
  preserving the verified payload, and the same signed artifact is installed
  with the installed port and upstream selection
- **THEN** the command and native supervision are restored and one accepting
  listener proves the artifact's release receipt and serving payload before
  `installed` is reported
- **AND** no transaction is created.

#### Scenario: A verified listener serves without running supervision

- **WHEN** a listener serving exactly that artifact remains while native
  supervision is absent or declared but not running
- **THEN** `install` binds native supervision and reports `installed` with that
  same listener's identity.

#### Scenario: The installation is not proved

- **WHEN** the artifact differs from the installed payload, the payload or
  command is not verified, the native service is running or unknown, the
  requested port differs, or another process listens on the installed port
- **THEN** `install` refuses without changing the command, native supervision,
  payload or journal.

#### Scenario: The restored listener cannot be proved

- **WHEN** supervision binds but no serving listener is proved within the
  deadline
- **THEN** `install` fails with one bounded lifecycle error
- **AND** the restored command and bound supervisor remain in place.

### Requirement: Rollback requires an answering successor before any change

`rollback` to the retained predecessor SHALL refuse before it creates a
transaction or changes any projection when no runtime answers on the installed
port.

#### Scenario: Nothing serves the successor

- **WHEN** a verified predecessor is retained but no runtime answers on the
  installed port
- **THEN** `rollback` fails with one bounded lifecycle error
- **AND** the selector, installed record, command, native supervision and
  journal are unchanged.

### Requirement: Default product roots are disjoint

The default state root, which holds the lifecycle lock and logs, SHALL lie
outside the default product data root on every supported platform, so that no
lifecycle command creates content in the root that installation and purge
verify.

#### Scenario: A fresh Windows host uses the default roots

- **WHEN** no root override is set on Windows
- **THEN** the data root is `%LOCALAPPDATA%\openai-responses-proxy` and the
  state root is `%LOCALAPPDATA%\openai-responses-proxy-state`
- **AND** taking the lifecycle lock before a fresh `install` leaves the data
  root absent.

#### Scenario: Every platform default is disjoint

- **WHEN** the product resolves its default roots on macOS, Linux or Windows
- **THEN** taking the lifecycle lock creates content only beneath the state root.

## MODIFIED Requirements

### Requirement: Recovery binds candidate, rollback, and live runtime

Recovery SHALL distinguish no transaction, an unmutated `prepared`
transaction, a `recovery_required` payload transition, and an invalid retained
transaction.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** No transaction SHALL be an idempotent successful no-op. A prepared
  transaction SHALL be closed only when its canonical journal is the sole
  transaction-root entry. Recovery of a selected mutated projection SHALL require
  one canonical journal, a fully verified current candidate bundle, the exact
  rollback command snapshot, and matching accepting runtime identity.

When a materialized reverse candidate was never selected, recovery SHALL prove
that the current generation selection is exactly the pre-transaction selection,
the installed state is bound to that active generation and release, the command
is owned by that generation's executable, the immutable payload identity is
valid, and the accepting non-draining runtime matches that payload. Only then
may it close the transaction without reading an unused rollback snapshot. It
SHALL preserve the selected payload, command, listener, and service. Any missing
proof or any other selection SHALL fail closed without mutation. Journal-only
closure SHALL remove only a transaction root whose canonical journal is its sole
entry. Lifecycle writers are serialized before transaction inspection or
mutation; processes that bypass the product-owned lifecycle boundary are outside
the supported concurrency contract.

Any existing but unverifiable transaction carrier SHALL fail closed without
mutation and identify whether the transaction root or journal is missing, a
symbolic link, the wrong filesystem type, malformed JSON, non-canonical JSON,
an unsupported schema, or invalid under the current schema.

The public recovery result SHALL be exactly one of `not_required`, `closed`,
`finalized`, `rolled_back`, or `purged`. `purged` requires that an interrupted
purge completed its verified payload removal. `finalized` requires the
accepting runtime to match the committed candidate's release, serving payload,
release receipt, and manifest identities. `rolled_back` requires either the
retained rollback identity or exact proof that the prior selected generation
remained terminal; neither outcome may be inferred from process presence alone.

#### Scenario: No transaction exists

- **WHEN** the exact transaction root is absent
- **THEN** recovery succeeds with state `not_required`
- **AND** changes no payload, command, listener, service, or filesystem entry
  other than the lifecycle lock in the state root.

#### Scenario: A prepared transaction contains only its canonical journal

- **WHEN** admission completed but payload mutation never began
- **THEN** recovery removes the transaction root without changing payload,
  command, listener, or supervision
- **AND** reports the transaction as closed.

#### Scenario: A prepared transaction contains additional content

- **WHEN** any file, directory, link, or ambiguous journal field exists beyond
  the canonical prepared journal
- **THEN** recovery fails closed and preserves the complete transaction root.

#### Scenario: An unselected reverse candidate has no rollback snapshot

- **WHEN** a materialized reverse candidate was never selected
- **AND** selection, installed state, command ownership, immutable payload, and
  accepting runtime all prove the prior terminal generation
- **THEN** recovery closes the orphaned transaction without reading a rollback
  command snapshot
- **AND** preserves the selected payload, command, listener, and service.

#### Scenario: The unselected reverse candidate cannot prove terminal state

- **WHEN** selection, installed state, command ownership, immutable payload, or
  accepting runtime differs from the prior terminal generation
- **THEN** recovery fails closed without removing the candidate or transaction
- **AND** does not read absence of a snapshot as permission to invent state.

#### Scenario: Another lifecycle writer is active

- **WHEN** install, recover, reload, rollback, or uninstall already owns the
  product lifecycle mutation boundary
- **THEN** another lifecycle mutation is rejected before reading or changing
  transaction state
- **AND** status and doctor remain available as read-only observations.

#### Scenario: All identities agree

- **WHEN** release, complete file inventory, serving digest, receipt, manifest
  digest, transaction, and runtime state match the selected candidate
- **THEN** recovery finalizes or restores the exact prior payload as required
- **AND** clears the hold only after terminal binding succeeds.

#### Scenario: Any identity differs

- **WHEN** a required byte, path, mode, digest, PID, state, or journal field
  differs
- **THEN** recovery fails closed without changing the payload or journal.

#### Scenario: The transaction carrier is invalid

- **WHEN** recovery observes a symbolic-link or non-directory transaction root,
  a missing or symbolic-link journal, malformed or non-canonical JSON, an
  unsupported schema, or invalid current-schema fields
- **THEN** recovery fails before any lifecycle mutation
- **AND** identifies the exact failed invariant
- **AND** leaves all retained files and the independently serving runtime
  unchanged.

### Requirement: Uninstall removes only proved product ownership

Uninstall SHALL remove only the exact service, processes, command projection,
and manifest-owned payload associated with the selected installation. An
absent installation SHALL be an idempotent successful no-op. An existing
payload root without valid ownership evidence SHALL be preserved and purge
SHALL fail closed.

#### Scenario: No installation exists

- **WHEN** no owned service, listener, command projection, payload root, or
  installed-state record exists
- **THEN** uninstall succeeds with state `not_installed`
- **AND** `--purge` has the same result without creating or deleting content
  other than the lifecycle lock in the state root.

#### Scenario: Unverified content occupies the payload root

- **WHEN** purge observes an existing payload root without a valid ownership
  manifest
- **THEN** it exits nonzero with a precise ownership diagnostic
- **AND** preserves every byte.

#### Scenario: Unknown content shares the install directory

- **WHEN** purge removes every manifest-owned file
- **THEN** unknown content remains untouched
- **AND** the command reports that the directory is not fully purged.

#### Scenario: The installed command link remains product-owned

- **WHEN** uninstall has proved service and process absence
- **AND** the command link still targets the exact installed executable
- **THEN** the command link is removed
- **AND** the payload is preserved unless `--purge` is requested.

#### Scenario: The command path changed ownership

- **WHEN** uninstall observes a foreign file, directory, or link at the command
  path
- **THEN** the path is preserved
- **AND** uninstall reports the ownership conflict without claiming complete
  removal.

### Requirement: Explicit rollback is one reverse lifecycle transaction

The rollback command SHALL require one syntactically valid target release.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** If
  the verified installed active release already equals that target, rollback SHALL
  succeed as an idempotent no-op without draining, starting a transaction, changing
  the generation selection, or rewriting the command. Otherwise the target SHALL
  equal the immutable predecessor bound to the current finalized successor.
  Rollback SHALL verify both selected payload identities, current installed state,
  command ownership, and their selector binding before mutation. It SHALL rebind
  the native service and complete a bounded listener handoff to the predecessor
  identity before reporting success. The returned predecessor PID SHALL be the only
  verified product listener when success is reported; finalized health alone SHALL
  NOT establish completion. Rollback SHALL not downgrade the user command or the
  minimum release admitted by the next signed-asset installation. Every other
  target SHALL be rejected before any lifecycle mutation.

#### Scenario: Requested release is already active

- **WHEN** rollback names the exact installed active release
- **THEN** it reports an unchanged terminal state
- **AND** performs no drain, handoff, transaction, selection, command, service,
  listener, or payload mutation.

#### Scenario: Exact predecessor rollback succeeds

- **WHEN** rollback names the sole retained predecessor release
- **AND** the current successor and retained predecessor both verify and the
  predecessor proves accepting, finalized runtime identity
- **THEN** rollback reports state `rolled_back` only after the predecessor PID
  is the sole verified product listener
- **AND** payload, installed state, service definition, and listener identify
  the predecessor
- **AND** the command still identifies the newer verified lifecycle control
  release
- **AND** the result reports the actual source and target releases
- **AND** the displaced successor becomes the one retained predecessor for a
  possible forward reversal.

#### Scenario: Requested release is not admissible

- **WHEN** rollback names neither the installed active release nor the verified
  retained predecessor
- **THEN** it fails before drain or mutation
- **AND** preserves the selected payload, command, listener, service, retained
  predecessor, and transaction state.

#### Scenario: Required target is omitted or malformed

- **WHEN** the caller omits `--to-release` or supplies an invalid release value
- **THEN** command parsing or the existing strict version authority rejects the
  request before lifecycle mutation.

#### Scenario: Post-rollback lifecycle control

- **WHEN** the operator invokes `status`, `doctor`, `recover`, or `rollback`
  through the installed command after a serving rollback
- **THEN** that command understands the current selector and installed-state
  schemas
- **AND** a release not newer than the retained control release is refused as a
  replay or downgrade.

#### Scenario: Retained evidence is absent

- **WHEN** no retained predecessor exists
- **THEN** rollback reports state `unavailable`
- **AND** changes no filesystem, process, service, command, or listener state
  other than the lifecycle lock in the state root.

#### Scenario: Retained evidence is unverifiable

- **WHEN** any carrier shape, byte, mode, digest, generation binding, current
  installed identity, service identity, or listener identity cannot be proved
- **THEN** rollback fails closed before mutation
- **AND** preserves the selected active and predecessor generations for
  inspection.

#### Scenario: Finalized health precedes listener convergence

- **WHEN** the predecessor reports finalized health while the displaced
  successor remains a verified listener
- **THEN** rollback continues bounded convergence and does not report success
- **AND** reports an indeterminate outcome if one sole predecessor listener
  cannot be proved within the bound.

#### Scenario: Reverse handoff has a proved failure

- **WHEN** rollback has selected the predecessor but successor retirement or
  predecessor readiness fails with a proved outcome
- **THEN** the transaction restores the displaced successor serving projection
  and stable command ownership
- **AND** does not report rollback success.

#### Scenario: Reverse handoff outcome is unknown

- **WHEN** neither predecessor finalization nor successor restoration can be
  proved
- **THEN** the active transaction is retained for `recover`
- **AND** no successful rollback or restoration claim is emitted.

### Requirement: Payload primitives have one semantic owner

Canonical paths and safe file I/O SHALL belong to `owned_files`; candidate
materialization to `candidate`; installed integrity and purge to `projection`;
rollback to `rollback`; journals and installed records to `records`; and
orchestration to `transaction`. No forwarding facade or private-name import
SHALL create a second authority. The four transaction roles SHALL remain
separate concrete modules rather than being folded into the orchestrator or
projected through a compatibility facade.

#### Scenario: A transaction mutates the payload

- **WHEN** it validates, snapshots, writes, verifies, restores, or finalizes
- **THEN** it calls the defining semantic owner directly.
