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

### Requirement: The macOS watchdog belongs to the user Background domain

The macOS supervisor SHALL install one watchdog in the current user's reachable
launchd domain with a Background session, without requiring a GUI login.
Legacy GUI migration SHALL prove carrier ownership, exact predecessor exit,
service absence and successor identity before reporting completion.

#### Scenario: A reachable user domain has no GUI login

- **WHEN** the operator invokes the native supervisor with a reachable user domain
- **THEN** installation uses that user domain and a Background session
- **AND** no GUI domain, new login session or administrator credential is required.

#### Scenario: An owned legacy GUI watchdog is replaced

- **WHEN** the exact selected installation has one verified legacy GUI watchdog
- **THEN** the predecessor exits before its carrier is replaced
- **AND** the successor is re-observed in the user domain with its exact process identity.

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
