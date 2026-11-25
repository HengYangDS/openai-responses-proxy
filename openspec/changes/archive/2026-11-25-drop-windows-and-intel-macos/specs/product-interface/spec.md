## MODIFIED Requirements

### Requirement: One self-contained product executable

OpenAI Responses Proxy SHALL expose one `openai-responses-proxy` executable in a
manifest-bound native bundle that contains the selected native supervision
adapter and runs without an installed Python interpreter, package environment,
source checkout, or repository script. Installation SHALL project that
executable into the current user's platform command directory as a native link,
not a wrapper, alias, shell-profile edit, or second copy.

#### Scenario: A released product selects its native service adapter

- **WHEN** the bundled executable runs `status` or begins installation on macOS
  or Linux
- **THEN** its adjacent frozen dependencies and platform adapter are available
  from the same verified bundle
- **AND** missing internal modules cannot be hidden as an unknown service state
- **AND** no traceback, warning, Python module name, or private path is emitted.

#### Scenario: A release artifact is incomplete

- **WHEN** native platform assembly cannot enumerate every required bundle file
- **THEN** the operation exits nonzero before lifecycle mutation
- **AND** the user receives one concise reinstall action
- **AND** the release gate rejects the artifact.

#### Scenario: A user invokes a released product

- **WHEN** the user runs help, `--version`, or `status` from a pristine directory
  with Python absent from `PATH`
- **THEN** the executable completes its documented behavior from the verified
  bundle
- **AND** no module path, virtual environment, source file, or missing-Python
  diagnostic appears.

#### Scenario: A verified release is installed

- **WHEN** installation finalizes a verified native payload
- **THEN** `openai-responses-proxy` is discoverable through the user's platform
  command directory
- **AND** it resolves to the exact installed executable
- **AND** no Python interpreter, source checkout, wrapper, or shell-profile
  mutation is required.

#### Scenario: A foreign command occupies the target

- **WHEN** the derived command path is not absent and is not an exact link to
  this product's installed executable
- **THEN** installation fails before payload mutation
- **AND** the foreign path remains unchanged.

### Requirement: Repository-owned verification separates wheel compatibility from native distribution

Python compatibility and quality sessions SHALL build and install the project
wheel, then exercise the complete behavior inventory through that installed
environment.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** They MUST NOT rebuild the native distribution. The release session
  SHALL be the sole native bundle build owner and SHALL prove every public
  command's help, valid and invalid inputs, human and JSON output, exit status,
  real handoff behavior, no-Python execution, prewarmed startup, and release-asset
  packaging. Release validation SHALL exercise the exact native executable that
  installation will serve, using an isolated installation root, native service
  identity, state root, HOME, and listener port. Native subprocess verification
  SHALL preserve the host operating-system runtime environment and override only
  the isolated paths owned by the test. A temporary copy or the canonical
  installed service SHALL NOT be treated as proof of the release candidate.

#### Scenario: Python and native gates prove distinct facts

- **WHEN** repository verification runs the supported Python matrix and release
  gate
- **THEN** each Python version proves the installed wheel and console executable
- **AND** exactly one release session builds and black-box tests the native
  bundle
- **AND** the release test prewarms and starts the exact installed executable
  within the configured installation deadline
- **AND** the complete public command matrix runs without consulting or mutating
  the canonical installation
- **AND** both surfaces retain their complete owned behavior tests.

#### Scenario: Compatibility evidence uses a published predecessor

- **WHEN** release compatibility verification is explicitly supplied one
  published signed predecessor asset and its external trust anchor
- **THEN** it installs and verifies that exact predecessor before deriving an
  isolated route-controlled fixture from the admitted executable bytes
- **AND** proves ordinary and streaming requests survive the forward upgrade
- **AND** never fabricates a predecessor by changing current release metadata.

#### Scenario: An operator upgrades a running installation

- **WHEN** a verified release is committed as the successor projection
- **THEN** the exact installed executable completes a bounded prewarm probe
- **AND** the handoff uses the operator's configured installation deadline.

#### Scenario: Native verification runs on a supported host

- **WHEN** a native executable test starts a child process on macOS or Linux
- **THEN** the child retains the host variables required by that operating
  system
- **AND** the test overrides only its isolated home, product roots, and command
  search path
- **AND** absence of Python from that command search path remains proven.

### Requirement: Human and machine interfaces share one result model

The installed executable SHALL render concise, task-oriented human output by
default and stable JSON only when `--json` is requested.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Every public command
  SHALL support both projections and SHALL preserve one semantic result and exit
  status model across them. Healthy absence, pending recovery, invalid evidence,
  degraded installation, and completed mutation SHALL be distinct outcomes.
  Human output SHALL use consistent sections, display-width alignment,
  actionable state-specific guidance, and no serialized object dump. Source
  modules, Python launch syntax, repository paths, and release-operator commands
  SHALL remain outside the end-user journey. Status SHALL report release identity
  from the verified installed-state record and command discoverability without
  consulting repository files or a second state authority. Runtime evidence SHALL
  be returned only when its PID is the sole listener owned by the selected
  installation. An installed command path SHALL be interpreted as a POSIX
  absolute path and symbolic link. Recovery SHALL identify the exact failed carrier invariant when the
  transaction root or journal is missing, a symbolic link, the wrong filesystem
  type, malformed JSON, non-canonical JSON, an unsupported schema, or invalid
  under the current schema. It MUST preserve those bytes, retain one stable
  \`recovery_state_invalid\` error code and read-only next action, and distinguish
  that evidence failure from the health of an independently serving runtime.

#### Scenario: An operator inspects the installed service

- **WHEN** the operator runs `openai-responses-proxy status`
- **THEN** the command presents release, payload, command, service, listener,
  and transaction state in a scannable layout
- **AND** `status --json` exposes the same semantics
- **AND** a recorded command path that is not a POSIX absolute path is not
  accepted as the installed command
- **AND** `doctor` classifies a missing or foreign command projection as an
  actionable failure
- **AND** a listener is healthy only when its runtime payload identity matches
  the currently committed installed candidate
- **AND** neither output exposes a Python module invocation or source-checkout
  requirement.

#### Scenario: The product is not installed

- **WHEN** `status`, `doctor`, `recover`, or `uninstall` observes no owned
  installation or transaction
- **THEN** each command identifies the absent state without calling it corrupt,
  unknown, recovered, or removed
- **AND** `doctor` recommends installation rather than reload
- **AND** `recover` and `uninstall` return successful explicit no-op results.

#### Scenario: Automation invokes any public command

- **WHEN** automation invokes `install`, `status`, `doctor`, `recover`, `reload`,
  `uninstall`, or `version` with `--json`
- **THEN** the command emits one stable JSON value and no human decoration
- **AND** successful lifecycle results use one `state` discriminator rather
  than parallel boolean or mode fields
- **AND** expected failures expose one stable error `code`, one concise
  `message`, and one directly executable `next` command
- **AND** expected failures remain nonzero without a traceback or warning.

#### Scenario: Another listener occupies the selected port

- **WHEN** loopback health responds but its PID is not the sole listener owned by
  the selected installation
- **THEN** status omits untrusted runtime evidence
- **AND** doctor reports an identity mismatch without treating that listener as
  this product.

#### Scenario: Retained recovery evidence is invalid

- **WHEN** a transaction root exists but its canonical journal is missing, a
  symbolic link, the wrong filesystem type, malformed JSON, non-canonical JSON,
  an unsupported schema, or invalid under the current schema
- **THEN** recovery returns the exact invalid carrier classification
- **AND** returns \`recovery_state_invalid\` with \`status --json\` as its next action
- **AND** it does not describe an independently serving runtime as unavailable
- **AND** it does not mutate or delete the retained bytes.

### Requirement: Repository automation has one portable semantic owner

Forge, release, quality, and contract behavior SHALL be implemented in the
repository's Python command and pytest surfaces. CI MAY select a platform or
supply credentials but SHALL NOT reimplement repository policy. Shell adapters
MAY exist only when required by the target operating system and SHALL contain no
product or repository policy.

#### Scenario: A developer verifies the repository on a supported platform

- **WHEN** the developer runs the documented repository verification command
- **THEN** the same Python and pytest owners execute on macOS and Linux
- **AND** no shell script of the repository carries repository policy

#### Scenario: A Shell owner is migrated

- **WHEN** its Python replacement and callers are complete
- **THEN** the Shell file is deleted in the same change
- **AND** no forwarding wrapper or parallel shell implementation remains
