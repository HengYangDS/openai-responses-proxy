## ADDED Requirements

### Requirement: The product boundary remains a narrow optional data plane

The Proxy SHALL accept and translate supported Responses traffic,
own its loopback listener and installed lifecycle, and expose bounded operational
commands. It SHALL NOT own Provider selection, credentials, client installation,
client configuration, model selection, conversation history, repository
lifecycle, or AIGW behavior. A client or control plane MAY select the Proxy as
one ordinary endpoint, but the Proxy SHALL install and operate independently.

#### Scenario: The Proxy is used without AIGW

- **WHEN** an operator supplies a valid upstream route and starts the installed product
- **THEN** request translation, status, diagnostics, lifecycle, and uninstall work
- **AND** no AIGW executable, profile, state, service, or configuration is read.

#### Scenario: A control plane composes with the Proxy

- **WHEN** an external control plane selects the Proxy loopback endpoint
- **THEN** the Proxy receives an ordinary supported request
- **AND** neither product imports, installs, starts, stops, or mutates the other.

### Requirement: Responses admission follows protocol rather than brand

Generic request admission SHALL depend on the declared Responses contract, not
on Codex identity or a Provider brand. A Chat Completions-only endpoint SHALL
NOT be admitted as a Responses Provider.

#### Scenario: A second Responses client uses the same data plane

- **WHEN** a non-Codex client sends a supported Responses request to a declared
  Provider route without Codex installed or configured
- **THEN** the same admission, response, streaming, and error contracts apply
- **AND** no Codex identity or history is required for that request.

### Requirement: Public operations have one precise result contract

Every public command SHALL return one typed semantic outcome shared by human and
JSON renderers. The outcome SHALL distinguish healthy absence, degraded state,
invalid input, unavailable recovery, required recovery, completed mutation, and
failed mutation, and SHALL identify only the safe next action owned by that
command.

#### Scenario: A public command cannot complete

- **WHEN** a documented precondition or external dependency is missing
- **THEN** the command names the failed boundary and one actionable next step
- **AND** emits no traceback, warning, internal type, private path, credential,
  request content, or unrelated usage dump.

#### Scenario: Lifecycle evidence contradicts a completed operation

- **WHEN** a reload result names a nonpositive process ID or the same predecessor
  and successor, or a cleanup result has a negative or non-integer stopped count
- **THEN** the public outcome boundary rejects the result before either renderer
- **AND** emits one bounded failure instead of successful operation output.

#### Scenario: Cleanup has no live process to stop

- **WHEN** valid cleanup evidence reports zero stopped processes
- **THEN** the public outcome remains successful in both human and JSON output
- **AND** does not invent a process or require a positive count.

#### Scenario: Installation fails and compensation also fails

- **WHEN** a fresh install fails and native cleanup or payload rollback cannot finish
- **THEN** the public failure retains both the initial and compensation boundaries
- **AND** native generation removal reports its numeric operating-system error
  without private paths or arbitrary exception text
- **AND** unavailable service admission does not trigger native cleanup
- **AND** existing recovery authority remains intact until exact cleanup succeeds.

### Requirement: The macOS watchdog survives restart in the user's login domain

The macOS supervisor SHALL install one watchdog whose carrier admits the Aqua and
Background sessions, registered in the user's GUI login domain when one exists and
otherwise in the reachable user domain with a Background session. Replacing a
watchdog registered in either domain SHALL prove carrier ownership, exact
predecessor exit, service absence and successor identity before reporting
completion.

#### Scenario: A reachable user domain has no GUI login

- **WHEN** the operator invokes the native supervisor with a reachable user domain
  and no GUI login
- **THEN** installation uses that user domain and a Background session
- **AND** no GUI domain, new login session or administrator credential is required.

#### Scenario: A GUI login restores the watchdog after restart

- **WHEN** the operator installs while the user has a GUI login
- **THEN** the watchdog is registered in that login's GUI domain from a carrier
  admitting the Aqua session
- **AND** launchd restores it at the next GUI login after a restart without
  operator action.

#### Scenario: An owned predecessor watchdog is replaced

- **WHEN** the exact selected installation has one verified watchdog in the user
  or GUI domain, including a Background-only predecessor
- **THEN** the predecessor exits before its carrier is replaced
- **AND** the successor is re-observed in the selected domain with its exact
  process identity.

### Requirement: macOS carrier mutations preserve ownership and preimages

The launch-agent carrier SHALL be a regular file bound to the selected home,
service and installed executable or canonical generation. Replacement and removal
SHALL recheck its preimage after service termination. Unknown ownership, symlinks,
competing registrations and failed native observations SHALL preserve state and
return a bounded product error.

#### Scenario: The carrier or registration is not owned by the selected installation

- **WHEN** an operation observes unknown carrier ownership or competing registrations
- **THEN** it refuses service and carrier mutation
- **AND** the original file bytes and foreign processes remain preserved.

### Requirement: macOS status observes the service independently of its carrier

Status SHALL observe applicable native service domains even when the carrier is
missing. A registered or running service SHALL retain that classification until
native service evidence proves absence.

#### Scenario: The carrier is missing while the watchdog remains registered

- **WHEN** the operator requests status after the carrier has been removed
- **THEN** the registered or running watchdog is reported from native service evidence
- **AND** the missing file alone is not evidence of service absence.

### Requirement: Diagnosis names only an admissible lifecycle action

`status` and `doctor` SHALL classify a verified installation that keeps its owned
native supervisor declaration while neither its supervisor nor a listener runs
as `stopped`, and SHALL name `reload` as its next action. `doctor` SHALL name
`install --help` only when installing the installed artifact restores a verified
payload's missing native supervision or command projection, and otherwise,
without a verified runtime, read-only `status --json`.

#### Scenario: A restart leaves the installation stopped

- **WHEN** the selected payload, installed record and command are verified, no
  transaction exists, the native service is declared but not running, its
  declaration binds the lifecycle control executable, and no process listens on
  the installed port
- **THEN** `status` and `doctor` report `stopped` and name `reload` in human and
  JSON output
- **AND** a completed `reload` reports `started` with one positive listener PID.

#### Scenario: No verified runtime remains outside the stopped state

- **WHEN** no verified runtime exists, the installation is not `stopped`, native
  supervision is declared, running or unknown, and the command is owned
- **THEN** `doctor` names `status --json` rather than `reload`
- **AND** `reload` refuses without changing native supervision or processes.

#### Scenario: Native supervision is absent after uninstall

- **WHEN** `uninstall` preserved a verified payload, so that no native
  supervision, command or listener remains
- **THEN** `doctor` names `install --help`
- **AND** installing the same signed artifact reports `installed`.

#### Scenario: Only the command projection is missing

- **WHEN** a verified payload keeps declared or absent native supervision, its
  command projection is absent, and no unverified listener holds the port
- **THEN** `doctor` names `install --help`
- **AND** installing the same signed artifact restores the command and native
  supervision.

#### Scenario: The payload or command cannot be verified

- **WHEN** no transaction requires recovery and the payload integrity fails or
  the command path belongs to another owner
- **THEN** `doctor` names `status --json` rather than `install --help` or
  `reload`.

### Requirement: Diagnosis of a serving installation names a converging action

With a verified serving runtime and an owned command projection, `doctor` SHALL
name `reload` only when native supervision is declared but not running or
outdated, `install --help` when it is absent, and read-only `status --json`
when it is unknown.

#### Scenario: The watchdog stopped while the listener serves

- **WHEN** a verified listener serves and its native supervisor is declared but
  not running
- **THEN** `status` reports `degraded` with service `installed` and `doctor`
  names `reload`
- **AND** after the completed reload `status` reports `running`.

#### Scenario: Native supervision is absent while the listener serves

- **WHEN** a verified listener serves while native supervision is absent
- **THEN** `doctor` names `install --help` rather than `reload`.

#### Scenario: Native supervision is unobservable

- **WHEN** a verified listener serves while native supervision is unknown
- **THEN** `doctor` names `status --json` rather than `reload`.

### Requirement: Status reports the native supervisor projection

`status` SHALL report a running native service as `outdated` when its owned
declaration binds the lifecycle control executable but differs from the
installed release's projection, and SHALL report the installation as `degraded`
when a running supervisor's declaration does not bind that executable. macOS
and Linux SHALL compare the owned definition exactly; Windows SHALL compare the
registered task action.

#### Scenario: An older installer rendered the running supervisor's definition

- **WHEN** a running supervisor's owned declaration binds the lifecycle control
  executable but differs from the installed release's projection
- **THEN** `status` reports `degraded` with service `outdated` and `doctor`
  names `reload`
- **AND** after the completed reload the declaration equals the installed
  release's projection.

#### Scenario: A running supervisor does not bind lifecycle control

- **WHEN** a running supervisor's owned declaration is absent or names another
  executable
- **THEN** `status` reports `degraded` with service `running`
- **AND** `doctor` names `status --json`.

#### Scenario: Task Scheduler normalizes an exported task

- **WHEN** an exported Windows task differs from the rendered task only outside
  its action
- **THEN** its projection remains current.

## MODIFIED Requirements

### Requirement: Local product closure is Forge-free

The repository SHALL keep local Git as the sole product source and SHALL make
GitLab and GitHub optional, independent publication peers. A selected Forge
SHALL receive the exact signed local commit object without changing author,
committer, parents, tree, message, or signature. ETHOS publication SHALL
advance remote `dev`, `main`, and each `proposal/*` ref through its own exact
compare-and-swap request; `candidate/dev` and `work/*` SHALL remain local-only.

#### Scenario: Both Forges are unavailable

- **WHEN** a clean accepted checkout has no reachable remote
- **THEN** local verification, packaging, installation, runtime acceptance,
  update, rollback, and uninstall remain executable
- **AND** no hosted publication fact is claimed.

#### Scenario: Either Forge is independently available

- **WHEN** GitLab or GitHub alone is selected for publication
- **THEN** it receives the exact local signed commit OID and tree
- **AND** the unavailable peer is neither read nor required.

#### Scenario: Both Forges are independently available

- **WHEN** both peers accept the same branch publication, in one request or in
  separate selected-peer requests
- **THEN** local, GitLab, and GitHub branch tips are the same commit OID
- **AND** each Forge retains independent authentication, CI, Release, and asset state.

#### Scenario: Source proof is complete

- **WHEN** exact-HEAD repository proof passes and the Change is archived
- **THEN** the governed landing command can atomically advance `candidate/dev`
- **AND** the permission does not authorize direct publication or runtime mutation
- **AND** no alternate integration path is introduced.
