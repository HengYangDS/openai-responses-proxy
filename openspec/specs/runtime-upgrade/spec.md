# Runtime Upgrade

## Purpose

Define the current native payload, transactional handoff, rollback, recovery,
and supervision invariants.

## Requirements

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

### Requirement: Listener configuration has one source

The runtime configuration owner SHALL define the default listener port and all
validated overrides. Installer, control, service, and uninstall code SHALL not
copy port, host, log, or concurrency policy.

#### Scenario: A port override is supplied

- **WHEN** an operator selects a valid port through the public CLI
- **THEN** installation, supervision, health, reload, and uninstall use that
  exact value consistently.

### Requirement: Native supervision is self-contained and portable

The released executable SHALL install and inspect its user service through the
native macOS or Linux adapter without an ambient Python interpreter,
optional process utility, source path, user identity, or workstation-specific
coordinate.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The default installation SHALL retain the public service identity;
  every alternate installation root SHALL use a deterministic identity derived
  from that root. Signal paths SHALL revalidate exact process identity
  immediately before mutation. Creation, observation, and teardown of one
  native service SHALL consume the same exact runtime context. Native acceptance
  on each supported operating system MUST exercise install, status, recovery,
  and uninstall through that platform's built artifact; source tests, mocks, and
  cross-compilation MUST NOT be reported as equivalent native product evidence.

#### Scenario: A supported host lacks development tools

- **WHEN** the product is installed on a clean supported host
- **THEN** service installation, listener discovery, handoff, status, and
  uninstall remain available from the released executable alone.

#### Scenario: An alternate root is installed for validation

- **WHEN** a signed asset is installed with a non-default product data root
- **THEN** native supervision SHALL use an identity unique to that absolute root
- **AND** installation SHALL not unload, replace, or report the default service
- **AND** uninstall SHALL address only the alternate identity and its listener.

#### Scenario: The default root is upgraded

- **WHEN** the signed installer targets the canonical product data root
- **THEN** it SHALL use the public service identity and current handoff protocol
- **AND** alternate validation identities SHALL remain untouched.

#### Scenario: A supported host accepts a native release

- **WHEN** the platform-built release artifact is installed into an isolated user root
- **THEN** the public status command reports the exact installed release and healthy owned service
- **AND** recovery reports the correct state without mutating healthy absence
- **AND** uninstall removes the exact owned service, processes, projection, payload, and command
- **AND** the host inventory has no net product residue after teardown

#### Scenario: A native lifecycle fails before completion

- **WHEN** installation, handoff, recovery, or an assertion fails on a supported host
- **THEN** bounded teardown uses the exact resolved service and process identities
- **AND** unrelated or canonical installations remain unchanged
- **AND** any unverifiable owned state remains explicit rather than being deleted by prefix

#### Scenario: A platform runner is unavailable

- **WHEN** one hosted platform cannot schedule a native acceptance job
- **THEN** that platform's product evidence remains unavailable
- **AND** successful evidence from another platform is not relabeled as proof for the unavailable platform

#### Scenario: Isolated native verification ends

- **WHEN** an isolated macOS or Linux lifecycle completes or aborts
- **THEN** teardown addresses only its exact service identity and carrier
- **AND** proves its owned watchdog, listener, and handoff processes have exited
- **AND** leaves the canonical service and listener unchanged
- **AND** leaves no net host service residue.

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

### Requirement: Native release artifacts are reproducible

The same accepted source tree, locked supply chain, platform, architecture, and
release inputs SHALL produce byte-identical native assets. Standard-library
modules with nondeterministic bytecode serialization SHALL use supported
PyInstaller collection modes rather than a custom archive rewriter.

#### Scenario: Independent Linux Forges build one release

- **WHEN** GitLab and GitHub build the same accepted tree with the locked Linux toolchain
- **THEN** their native archives and executables have identical SHA-256 digests.

### Requirement: Canonical supervision precedes payload mutation

Installation SHALL require the current native listener and its native
supervisor to identify the canonical installed executable before committing
candidate payload bytes. No alternate launcher, migration bridge, wrapper, or
platform-specific compatibility path SHALL participate in current upgrade
admission.

#### Scenario: A current runtime is upgraded

- **WHEN** the sole listener has current release identity
- **THEN** its native supervisor already declares the canonical installed
  executable before candidate payload mutation
- **AND** any other supervisor target fails closed without changing the
  installed payload.

### Requirement: Handoff finalization observes the exact successor

After commit, the controller SHALL read bounded health snapshots through the
shared listener until the complete expected successor identity is served. A
snapshot from the retiring process, a transient socket failure, or a transient
health read failure SHALL be treated as an observation to retry, not success or
immediate failure. Deadline expiry SHALL identify the failed lifecycle phase
without including exception messages, request content, headers, credentials,
or upstream payloads.

#### Scenario: A health read fails during ownership transfer

- **WHEN** a post-commit health read raises an ordinary exception
- **THEN** the controller continues bounded observation
- **AND** finalizes only after the exact successor PID and payload identity are
  accepting and not draining
- **AND** rolls back if the deadline expires without that proof.

#### Scenario: The retiring listener answers during ownership transfer

- **WHEN** the first post-commit health snapshot still identifies the retiring
  process
- **THEN** the controller continues bounded observation
- **AND** finalizes only after the exact successor PID and payload identity are
  accepting and not draining.

#### Scenario: Successor observation does not converge

- **WHEN** the deadline expires or health observation fails
- **THEN** the transaction follows its rollback or recovery-required contract
- **AND** operational output records only the failed phase and exception class.

#### Scenario: The supervisor target is noncanonical

- **WHEN** installation observes any supervisor target other than the canonical
  installed executable
- **THEN** it rejects the upgrade before service or payload mutation
- **AND** the same invariant applies on macOS and Linux.

### Requirement: Runtime carrier remains install-generation stable

The supported predecessor installer and successor payload SHALL implement one
stable, secret-free `runtime-config.json` contract. Operating-system service
definitions SHALL remain derived projections and SHALL NOT expand that product
carrier with host-only ownership coordinates.

#### Scenario: A predecessor writes the successor carrier

- **WHEN** a supported published predecessor installs a newer signed payload
- **THEN** the predecessor writes the same current runtime-carrier schema that
  the successor consumes
- **AND** the successor activates without a compatibility parser or migration
  branch.

#### Scenario: macOS service ownership is projected

- **WHEN** installation creates or removes one launchd service
- **THEN** its exact plist path and `HOME` projection come from the live runtime
  context used by that operation
- **AND** the persisted product carrier remains platform-neutral.

### Requirement: Payload prewarm uses a private stable protocol

The installer SHALL prewarm the exact committed successor executable through a
private, side-effect-free executable role whose grammar is independent of the
public CLI. A public command rename or removal SHALL NOT change that protocol.
When an installed predecessor predates the stable role and cannot invoke the
successor, the operator SHALL bootstrap with the verified successor executable;
the product SHALL NOT retain a public compatibility alias solely for that
historical installer.

#### Scenario: Current installer prewarms a successor

- **WHEN** an admitted successor has been committed inside the rollback domain
- **THEN** the installer invokes the private prewarm role on that exact executable
- **AND** the role exits without reading runtime state, starting supervision, or
  changing product data.

#### Scenario: Public CLI grammar changes

- **WHEN** a release adds, renames, or removes a public command or option
- **THEN** the installer-to-successor prewarm invocation remains unchanged
- **AND** release verification fails if it is coupled to the public grammar.

#### Scenario: Historical installer lacks the stable role

- **WHEN** an installed predecessor cannot activate a breaking successor because
  it uses retired public syntax
- **THEN** the verified successor executable is used once to perform the upgrade
- **AND** no public compatibility command or permanent migration parser is added.

### Requirement: macOS lifecycle leaves no new persistent service projections

On macOS, each bounded native lifecycle SHALL leave the product-owned launchd
registration, process, plist, and override sets unchanged after exact service
teardown. Teardown SHALL address one fully qualified service target and SHALL
NOT reset a launchd domain, match a service-name prefix, or change the canonical
service while cleaning an alternate installation. Historical overrides created
by older product generations SHALL be handled only by a separate exact-label
host migration.

#### Scenario: An isolated macOS lifecycle ends

- **WHEN** an alternate-root lifecycle succeeds, fails, times out, or is interrupted
- **THEN** exact-label teardown removes its service registration, owned processes, and plist
- **AND** the product-owned override set has no net growth
- **AND** the pre-existing canonical service projection and listener remain unchanged
- **AND** the host has no net noncanonical service residue from that lifecycle.

### Requirement: Explicit rollback is one reverse lifecycle transaction

The rollback command SHALL require one syntactically valid target release.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** If
  the verified installed active release already equals that target, rollback SHALL
  succeed as an idempotent no-op without starting a handoff or a transaction, changing
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
- **AND** performs no handoff, transaction, selection, command, service,
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
- **THEN** it fails before any handoff or mutation
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

### Requirement: Native supervision projects the terminal generation

After listener handoff proves the terminal admission owner, installation SHALL
replace the platform-native supervisor with one running watchdog generation
executing that exact payload.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Product runtime identity SHALL be reconstructed
  from the committed, secret-free `runtime-config.json`; operating-system service
  definitions SHALL remain derived projections and SHALL NOT duplicate product
  configuration. A watchdog that starts a listener SHALL retain and poll its
  process handle so an exited child is reaped. The transaction SHALL remain the
  recovery authority until the configured and running supervisor identity is
  proved. If handoff rolls back, installation SHALL restore the predecessor
  payload and prove native supervision from that payload before closing the
  transaction.

On macOS, installation SHALL bind the exact GUI-domain service, prove any
predecessor watchdog PID has exited, bootstrap the current plist, and re-read
the exact successor PID. Plist registration, executable-path equality, or a
successful platform command alone SHALL NOT establish convergence.

#### Scenario: One runtime carrier projects to native service managers

- **WHEN** a committed payload is installed on macOS or Linux
- **THEN** the watchdog and native-service adapter reconstruct the same exact
  executable, installation root, log root, and service identity from
  `runtime-config.json`
- **AND** launchd or systemd user services contain only the
  native invocation and service-manager metadata
- **AND** no platform definition becomes a second product configuration source.

#### Scenario: An installed macOS watchdog is replaced during upgrade

- **WHEN** candidate payload bytes have committed while an earlier watchdog
  generation is registered
- **THEN** after listener handoff proves the terminal admission owner, the
  installer boots out the exact prior launchd service
- **AND** proves the predecessor PID is absent within a bounded deadline
- **AND** bootstraps the current plist into the exact GUI domain
- **AND** accepts only a distinct running PID returned and re-observed for the
  exact service label
- **AND** leaves the independent terminal listener serving throughout
  replacement.

#### Scenario: A current native runtime is upgraded

- **WHEN** the candidate listener has proved accepting terminal ownership
- **THEN** the native supervisor is replaced by a generation executing the
  candidate payload
- **AND** the transaction closes only after the exact service registration,
  executable, and running generation are proved.

#### Scenario: A watchdog-owned listener exits

- **WHEN** a listener spawned by the resident watchdog reaches a terminal
  process state
- **THEN** the watchdog polls its retained process handle
- **AND** no zombie process remains owned by that watchdog.

#### Scenario: Handoff rolls back before supervisor replacement

- **WHEN** successor handoff fails with a proved rollback outcome
- **THEN** the predecessor payload is restored
- **AND** native supervision is proved to execute that restored payload before
  the transaction closes.

#### Scenario: Launchd cannot prove generation replacement

- **WHEN** bootout, predecessor exit, bootstrap, kickstart, or successor PID
  observation fails or is ambiguous
- **THEN** installation fails with an actionable lifecycle error
- **AND** does not report native supervision as converged.

#### Scenario: An isolated native lifecycle ends

- **WHEN** a noncanonical installation succeeds, fails, times out, or raises
- **THEN** teardown removes the exact service registration and projection path
  used at creation
- **AND** proves every process owned by that exact service has exited
- **AND** leaves the canonical service and listener unchanged
- **AND** the set of noncanonical host service projections has no net growth.

#### Scenario: Purge encounters residual control-root content

- **WHEN** uninstall removes every verified generation and installed-state carrier
- **AND** content without current ownership evidence remains in the control root
- **THEN** purge preserves that content and exits nonzero
- **AND** a later install rejects the nonempty root before claiming a payload transaction.

### Requirement: Transactional handoff is the sole generation transition

A running upgrade SHALL bind the health snapshot to exactly one listener owned
by the installed executable, commit the admitted prewarmed bundle, and request
transactional handoff before changing native supervision.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** A successor SHALL
  prove its PID, executable, release, manifest,
  serving aggregate, receipt, accepting state, and non-draining state. The
  handoff child SHALL own only listener transfer and runtime identity; it SHALL
  NOT mutate the platform-native supervisor. Every private service role SHALL
  activate one existing, validated `runtime-config.json` located in the installed
  executable's payload root before importing or starting product runtime behavior.
  Environment variables are a projection of that carrier, not an alternate source
  and not an input from which a missing carrier may be created.

#### Scenario: Every private role activates the same carrier

- **WHEN** the listener, handoff child, or watchdog starts from an admitted
  installed executable
- **THEN** it resolves the carrier from that executable
- **AND** validates the carrier before product startup
- **AND** replaces inherited product settings with the carrier projection.

#### Scenario: A private role lacks a valid carrier

- **WHEN** any private role cannot read one valid executable-owned carrier
- **THEN** startup fails closed before listener, handoff, or watchdog behavior
  begins
- **AND** no private role creates a carrier from inherited environment variables
  or platform defaults.

#### Scenario: A current release upgrades successfully

- **WHEN** the sole verified listener supports handoff and the successor proves
  the admitted identity
- **THEN** installation writes the successor carrier and requests listener
  handoff without replacing the predecessor supervisor
- **AND** after the successor owns admission, native supervision is rebound to
  that exact successor and its configured and running executable identity is
  proved
- **AND** the handoff child does not restart or replace its own supervisor
- **AND** startup performs no first-run bundle extraction
- **AND** at most one listener accepts requests at each barrier.

#### Scenario: Supervisor rebinding fails after successful handoff

- **WHEN** the platform-native supervisor cannot be proved to declare the
  committed successor
- **THEN** the successor remains the sole serving generation
- **AND** the payload transaction remains recoverable
- **AND** no finalized claim is emitted.

#### Scenario: Handoff rolls back

- **WHEN** failure resolution proves the original runtime resumed
- **THEN** the exact complete rollback inventory is restored
- **AND** native supervision is rebound to the restored predecessor
- **AND** the transaction closes only after that supervisor identity is proved
- **AND** the operation fails.

#### Scenario: Handoff outcome is unknown

- **WHEN** neither finalization nor rollback can be proved
- **THEN** candidate and rollback bundle bytes remain transaction-bound for
  recovery
- **AND** native supervision is not treated as lifecycle authority
- **AND** no success or rollback claim is emitted.

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
  captured predecessor still owns the listener, admits requests and has no
  handoff in flight, and the controller holds evidence that no handoff of its
  request is to come
- **AND** without that proof the transaction is preserved for recovery as an
  unknown outcome
- **AND** the controller sends the predecessor no request that changes its
  admission.

#### Scenario: A listener refuses a handoff request before beginning it

- **WHEN** a listener answers a handoff request with an error status without
  beginning the handoff
- **THEN** the controller reads that the listener began nothing and rolls the
  payload back without waiting out the handoff deadline
- **AND** this holds for a listener that an earlier handoff created, whose
  health still shows that finished handoff.

#### Scenario: A handoff request ends without the listener's answer

- **WHEN** the controller is interrupted or loses its connection before the
  listener answers the handoff request
- **THEN** a listener at rest, idle or showing its finished handoff, SHALL NOT
  count as proof that nothing began, because the listener may have the request
  and be about to begin
- **AND** a handoff that begins is followed to its end, and otherwise the
  request is settled as "A failed handoff request is settled by evidence of
  the listener's part" requires, or the transaction is preserved for recovery
  as an unknown outcome.

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

### Requirement: A refused rollback leaves no transaction

A rollback that is refused before any projection SHALL close the transaction
that it opened, so that the next command needs no recovery.

#### Scenario: The running runtime cannot hand off

- **WHEN** `rollback` opens its transaction and the running runtime is then
  refused
- **THEN** the journal is absent after the failure
- **AND** the selector and the installed record are unchanged.

### Requirement: A failed handoff request is settled by evidence of the listener's part

After a handoff request has failed, the controller and `recover` SHALL read a listener at rest, idle or showing
a finished handoff, as proof that no handoff of that request is to come only together with evidence of the
listener's part in it: the request was never sent, the listener refused or acknowledged it, its handoff was
seen to begin and end, or the time in which the listener may begin it has run out. A listener SHALL begin a
handoff only within that time.

#### Scenario: A handoff request is never sent

- **WHEN** the controller fails before it writes the first byte of its handoff request, the opening of the
  request's connection included
- **THEN** it reads that the listener can begin nothing of that request
- **AND** it rolls the payload back without waiting once health shows the captured listener at rest.

#### Scenario: The controller ends the connection of its handoff request

- **WHEN** the controller ends the connection of a handoff request, after an answer or without one, or the
  controller's process ends
- **THEN** the connection ends with a reset and not with an ordinary close, so that a listener which does not
  have the connection yet never receives the request
- **AND** a connection that cannot be made to end so carries no request.

#### Scenario: A listener rolls back a handoff that it acknowledged

- **WHEN** a listener acknowledges a handoff request, the handoff fails before its successor commits, and the
  listener's handoff session returns to idle
- **THEN** the controller reads that the handoff began and ended
- **AND** it rolls the payload back without waiting out the handoff deadline.

#### Scenario: An unanswered request is seen to begin and end

- **WHEN** a handoff request ends without the listener's answer, and the controller then reads the request's
  transaction in the listener's handoff session and afterwards an idle session
- **THEN** the controller reads that the handoff began and ended and rolls the payload back
- **AND** a session that names another transaction is no sight of this request.

#### Scenario: A listener refuses a request that reaches its handoff too late

- **WHEN** a handoff request reaches the begin of its handoff later than the request's own timeout after the
  listener accepted the request's connection
- **THEN** the listener refuses the request with HTTP 408 and the error
  `handoff_request_expired`
- **AND** its handoff session is unchanged.

#### Scenario: An unanswered request runs out of time

- **WHEN** a handoff request ends without the listener's answer, the request's timeout and one second have
  passed since a health read that followed the request showed the captured listener at rest, and a health
  read that is asked after that time shows it at rest
- **THEN** the controller reads that the listener can no longer begin that request and rolls the payload back
- **AND** before that time a listener at rest proves nothing, and a handoff that begins is followed to its
  end.

#### Scenario: Recovery waits out a handoff request that may be pending

- **WHEN** `recover` finds a transaction that reached activation and a predecessor at rest
- **THEN** it restores the prior selection only on a health read that it asks after the longest timeout that a
  handoff request can carry and one second have passed since its first read returned, and that still shows
  the predecessor at rest
- **AND** a successor that committed in that time is finalized, and a handoff that is in flight then leaves
  the transaction for a later `recover`
- **AND** a transaction that did not reach activation requested no handoff and is restored without that wait.

#### Scenario: Recovery reads a handoff that is done or in flight

- **WHEN** health shows the predecessor's listener with a handoff in flight, or with the finished handoff of
  the transaction that `recover` decides, or shows a successor that its predecessor has not finalized
- **THEN** `recover` restores nothing and finalizes nothing, and the transaction stays for a later `recover`
- **AND** a predecessor that still shows the finished handoff which created it is at rest.

#### Scenario: Recovery says that it waits

- **WHEN** `recover` begins that wait in its human form
- **THEN** it writes one line to standard error that names the wait and its length
- **AND** with `--json` it writes nothing to standard error, and its result on standard output is unchanged
  in either form.

### Requirement: A payload is read only through a selected generation

The lifecycle SHALL read an installed payload only through the generation that the selector under the stable
control root names. Payload files at the control root's own level, without a selector and without a transaction,
SHALL be unverified content, whatever manifest accompanies them.

#### Scenario: Payload files lie at the control root without a selector

- **WHEN** the control root holds an executable, a payload manifest, a runtime carrier and an installed record at
  its own level, with no selector and no transaction
- **THEN** `status` reports neither `running` nor `stopped` and names no verified payload
- **AND** `uninstall --purge` exits nonzero with an ownership diagnostic and preserves every byte
- **AND** `rollback` and `reload` change no file, service or process.

#### Scenario: A serving process was loaded from outside the generation store

- **WHEN** a serving process whose payload does not lie in a generation of a control root resolves the selected
  payload for a handoff
- **THEN** the resolution fails before a successor is started.

### Requirement: A successful upgrade retains exactly one predecessor generation

A successful upgrade SHALL retain exactly one verified predecessor generation
after the successor has proved accepting runtime identity.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** One atomic selector
  under the stable control root SHALL be the sole authority naming the active
  immutable generation and its optional predecessor. Installed state and command
  projection SHALL remain stable control surfaces outside both generations. A
  transaction snapshot MAY exist only as temporary bootstrap or recovery
  evidence and SHALL NOT become another retained product store. A subsequent
  successful upgrade SHALL replace the older predecessor in the selector. Fresh
  installation SHALL select no predecessor.

#### Scenario: Upgrade finalizes successfully

- **WHEN** the successor payload, command, native supervisor, and listener have
  proved the committed successor identity
- **THEN** finalization atomically selects the successor as active and the
  displaced active generation as its sole predecessor
- **AND** removes the active transaction without copying either selected
  generation into a parallel rollback store.

#### Scenario: Finalization is interrupted around selection

- **WHEN** finalization stops after the predecessor generation is materialized
  or after its selector is committed
- **THEN** the active transaction remains the sole recovery authority
- **AND** retry advances the same generation to the terminal selected state
- **AND** the previously selected generation is not removed before the new
  selector is durable.

#### Scenario: A second upgrade succeeds

- **WHEN** an installation with a retained predecessor upgrades again
- **THEN** the newly displaced release becomes the sole retained predecessor
- **AND** no older rollback history remains.

### Requirement: Only the signed-asset installer admits a different release

Only the signed-asset installer SHALL admit a different release.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The payload
  transaction SHALL verify and prewarm the exact committed successor executable
  inside the rollback domain before requesting handoff. Handoff readiness SHALL
  use the configured bounded installation deadline without an independent,
  shorter startup cap. Installed control SHALL observe, reload, recover, or remove
  the current product but SHALL NOT accept arbitrary release bytes. Forge
  availability SHALL NOT be an installation input. The payload transaction SHALL
  coordinate the selected serving payload, installed-state record, and native
  command as one rollback domain. The selector SHALL determine the active
  serving generation and its sole predecessor. The command SHALL resolve to the
  newest verified release among those selected generations so serving rollback
  cannot downgrade lifecycle control. The command SHALL be a symbolic link,
  admitted only when it identifies that exact control executable.

#### Scenario: An operator installs a release

- **WHEN** one signed native archive and its external trust anchor are supplied
- **THEN** the installer verifies and applies the release locally
- **AND** a Forge, Git, Python, uv, Nox, ETHOS, a client control plane, and a
  source checkout are not runtime dependencies.

#### Scenario: A fresh installation fails after payload projection

- **WHEN** payload bytes and the platform-native command are projected
- **AND** native service startup fails
- **THEN** rollback removes the candidate payload and command
- **AND** the pre-install absence is restored exactly.

#### Scenario: An upgrade handoff fails

- **WHEN** an existing release is upgraded and successor proof fails
- **THEN** rollback restores the prior serving payload and stable command
  ownership exactly
- **AND** foreign content remains unchanged.

#### Scenario: A cold native successor starts within the configured deadline

- **WHEN** the committed successor needs more than ten seconds for its first start
- **AND** it returns `READY` within the configured installation deadline
- **THEN** the upgrade continues to exact successor identity proof
- **AND** an arbitrary transport cap does not force rollback.

#### Scenario: Exact successor prewarm fails

- **WHEN** the executable committed to the candidate projection fails its bounded probe
- **THEN** the transaction restores the prior projection and command ownership
- **AND** the current verified listener remains available.

### Requirement: Supported native lifecycles are behaviorally symmetric

macOS and Linux SHALL expose the same public install, status, doctor,
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

#### Scenario: Generation retirement preserves operator content

- **WHEN** rollback discards a candidate or finalization retires an older generation
- **THEN** the transaction journal records exact file paths and digests before deletion
- **AND** candidate ownership is captured before materialization, so partial writes remain recoverable
- **AND** recovery uses that original inventory without requiring a complete discarded manifest
- **AND** replaced files, symbolic links, unlisted files, and unlisted empty directories remain untouched and prevent successful cleanup.

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

#### Scenario: A retained command snapshot changes before a transaction effect

- **WHEN** activation, rollback, or recovery needs a command snapshot
- **THEN** it reads one unchanged regular file without following symbolic links
- **AND** verifies those exact bytes against the journal's original snapshot digest
  before parsing or changing a generation, command, payload, or native service
- **AND** a replaced file, linked carrier, or digest mismatch leaves all retained
  transaction state and unrelated commands unchanged.

### Requirement: Capable handoff preserves request admission

A runtime-to-runtime handoff SHALL transfer listener ownership without changing
the request-admission state.

#### Scenario: A capable runtime is upgraded or rolled back under load

- **WHEN** a verified current runtime advertises both selected-generation and
  admission-preserving handoff capabilities
- **THEN** upgrade or rollback uses that handoff while new requests continue to
  be admitted and already accepted requests reach terminal responses
- **AND** no request is refused because of the transition.

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

- **WHEN** a `stopped` installation is reloaded on macOS or Linux
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

#### Scenario: Every platform default is disjoint

- **WHEN** the product resolves its default roots on macOS or Linux
- **THEN** taking the lifecycle lock creates content only beneath the state root.

### Requirement: Lifecycle control admits only a local native controller

Handoff control SHALL accept a request only from a loopback client whose `Host`
names a loopback address and that carries no `Origin`. A web page, including
one that reaches the listener through DNS rebinding, SHALL NOT start a handoff.

#### Scenario: A web page posts a lifecycle control request

- **WHEN** a control request carries an `Origin` header or a `Host` that is not
  a loopback address
- **THEN** the proxy refuses it with HTTP 403
- **AND** handoff state remains unchanged.

### Requirement: Handoff control receipt is bounded

The handoff control endpoint SHALL read a declared envelope of at most 32 KiB
within five seconds, and SHALL NOT read beyond its declared length.

#### Scenario: A controller stalls while it sends the envelope

- **WHEN** a handoff control request declares a body that does not arrive
  completely within five seconds
- **THEN** the proxy answers HTTP 408, closes that connection and starts no
  handoff
- **AND** the serving listener keeps its process identity and admission.

### Requirement: Runtime settings persist every selected budget

A fresh native runtime configuration SHALL persist the selected Responses
body, catalog and SSE budgets, and later contexts SHALL keep them despite
unrelated ambient replacements. A configuration that lacks a budget field
SHALL be refused unchanged with the removal and reinstallation action.

#### Scenario: A successor reads retained native runtime settings

- **WHEN** a successor reads the configuration that an installation persisted
- **THEN** later contexts preserve its stored values rather than taking
  unrelated ambient replacements
- **AND** catalog budgets leave Responses unchanged, and upgrades retain a
  selected SSE byte budget.

#### Scenario: A configuration lacks a budget field

- **WHEN** a native runtime configuration lacks a body, catalog or SSE budget
  field
- **THEN** the reader refuses it without rewriting the file
- **AND** the failure names removal with the release that wrote it, then a
  fresh installation.
