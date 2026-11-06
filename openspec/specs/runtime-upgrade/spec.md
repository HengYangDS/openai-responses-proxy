# Runtime Upgrade

## Purpose

Define the current native payload, transactional handoff, rollback, recovery,
and supervision invariants.

## Requirements

### Requirement: Source-side upgrade authority

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
  cannot downgrade lifecycle control. The command SHALL be a symbolic link on
  POSIX and a `.cmd` launcher on Windows; each form SHALL be admitted only when it
  identifies that exact control executable.

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

#### Scenario: Windows projects the user command

- **WHEN** installation runs on Windows
- **THEN** the command path is a `.cmd` launcher whose exact bytes invoke the
  exact control executable
- **AND** status, rollback, and uninstall classify ownership by those exact bytes
- **AND** no symbolic-link privilege or copied executable is required.

#### Scenario: A cold native successor starts within the configured deadline

- **WHEN** the committed successor needs more than ten seconds for its first start
- **AND** it returns `READY` within the configured installation deadline
- **THEN** the upgrade continues to exact successor identity proof
- **AND** an arbitrary transport cap does not force rollback.

#### Scenario: Exact successor prewarm fails

- **WHEN** the executable committed to the candidate projection fails its bounded probe
- **THEN** the transaction restores the prior projection and command ownership
- **AND** the current verified listener remains available.

### Requirement: The installed payload has one current shape

The installed payload SHALL contain one prewarmed native bundle under `bin/`,
`providers.toml`, their complete manifest, signed-release receipt, and finalized
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
`finalized`, or `rolled_back`. `finalized` requires the accepting runtime to
match the committed candidate's release, serving payload, release receipt, and
manifest identities. `rolled_back` requires either the retained rollback
identity or exact proof that the prior selected generation remained terminal;
neither outcome may be inferred from process presence alone.

#### Scenario: No transaction exists

- **WHEN** the exact transaction root is absent
- **THEN** recovery succeeds with state `not_required`
- **AND** changes no payload, command, listener, service, or filesystem entry.

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
  predecessor SHALL drain the current listener and use bounded native-generation
  replacement, because the retained predecessor is not required to implement the
  current release's hot-handoff capability.

#### Scenario: A retained predecessor predates the current handoff capability

- **WHEN** an operator rolls back from the current release to its selected
  predecessor
- **THEN** the current listener drains before its exact process generation exits
- **AND** native supervision starts the retained predecessor generation
- **AND** rollback succeeds only after that generation proves its exact accepting
  runtime identity
- **AND** the predecessor is not asked to consume a newer handoff protocol.

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
rollback to `rollback`; journals to `state`; and orchestration to `transaction`.
No forwarding facade or private-name import SHALL create a second authority.
The four transaction roles SHALL remain separate concrete modules rather than
being folded into the orchestrator or projected through a compatibility facade.

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
native macOS, Linux, or Windows adapter without an ambient Python interpreter,
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

- **WHEN** an isolated macOS, Linux, or Windows lifecycle completes or aborts
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
- **AND** `--purge` has the same result without creating or deleting content.

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
- **AND** the same invariant applies on macOS, Linux, and Windows.

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

### Requirement: Successful upgrade retains one exact predecessor

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

#### Scenario: The retained-generation transition is introduced

- **WHEN** the published predecessor predates retained-generation finalization
- **THEN** the verified successor executable drives that one upgrade
- **AND** preserves the predecessor payload bytes while deterministically
  projecting its secret-free runtime carrier to the immutable generation root
- **AND** establishes the current carrier without synthesizing historical state
- **AND** later adjacent upgrades return to installed-release ownership.

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
- **AND** changes no filesystem, process, service, command, or listener state.

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

### Requirement: Repeated lifecycle transitions use declared runtime capability

The deployment controller SHALL select one strategy from verified runtime
identity and explicit handoff capability.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Only a runtime declaring
  `selected-generation-handoff` MAY use shared-listener handoff because that
  capability proves the predecessor resolves and launches the selector-bound
  candidate payload rather than its own executable root. A complete, verified
  runtime without that capability SHALL use a bounded native process-generation
  replacement. An incomplete or inconsistent runtime SHALL be unsupported and
  SHALL NOT be mutated.

#### Scenario: Runtime declares selected-generation handoff

- **WHEN** a runtime projects the `selected-generation-handoff` capability
- **THEN** the next upgrade or rollback may use the shared-listener handoff
- **AND** successor acceptance still requires the exact payload and process
  identity.

#### Scenario: Verified predecessor lacks selected-generation handoff

- **WHEN** a published predecessor has complete identity but does not declare
  the `selected-generation-handoff` capability
- **THEN** deployment captures its exact listener process generation before
  writing candidate state
- **AND** rebinds native supervision, retires only that generation, and proves
  one successor listener within the configured bound
- **AND** preserves the transaction for recovery if predecessor exit is not
  proved.

#### Scenario: Native-generation successor does not become ready

- **WHEN** the bounded replacement cannot prove the successor listener
- **THEN** deployment restores the predecessor payload and service projection
- **AND** reports no successful upgrade or rollback.

#### Scenario: Runtime identity is incomplete

- **WHEN** listener, payload, protocol, transaction, or process identity is
  absent or inconsistent
- **THEN** no handoff or native-generation replacement is attempted
- **AND** the operator receives one precise incompatibility result.

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

- **WHEN** a committed payload is installed on macOS, Linux, or Windows
- **THEN** the watchdog and native-service adapter reconstruct the same exact
  executable, installation root, log root, and service identity from
  `runtime-config.json`
- **AND** launchd, systemd user services, or Task Scheduler contain only the
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
