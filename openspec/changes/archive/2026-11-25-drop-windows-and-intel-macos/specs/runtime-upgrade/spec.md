## ADDED Requirements

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

## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: Source-side upgrade authority

**Reason**: Its Windows launcher clause and scenario leave with Windows support, and OpenSpec keeps every scenario of a modified requirement.

**Migration**: "Only the signed-asset installer admits a different release" keeps every other statement and scenario of it.
