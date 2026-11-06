# repository-organization Specification

## Purpose

Define the repository authorities, development and release boundaries, documentation structure, and portable quality surfaces that keep the proxy lean and reproducible.

## Requirements

### Requirement: One release identity

Proxy SHALL use tracked `VERSION` as the sole product release identity for package metadata, artifact names, changelog headings, tags, and installation records.

#### Scenario: Release metadata is consistent

- **WHEN** local or hosted release verification runs
- **THEN** every release surface resolves the same SemVer value from `VERSION`

#### Scenario: A competing version is introduced

- **WHEN** a tool or workflow supplies a conflicting inferred version
- **THEN** verification fails before asset creation or publication

### Requirement: Governed release-branch convergence

The repository SHALL advance local release `main` from accepted `dev` only
through the adopted package-only ETHOS closeout command. Proxy source SHALL NOT
define a second release-root mutation command. The transition SHALL be local,
exact-head guarded, and independent of both Forge publication planes.

#### Scenario: Candidate is proved

- **WHEN** `candidate/dev` and accepted `dev` identify the same clean, proved
  commit and local `main` is its ancestor
- **THEN** ETHOS closeout MAY fast-forward `main` by exact compare-and-swap
- **AND** no remote ref, tag, Release, asset, client, or Codex session state is
  changed.

#### Scenario: Local transition facts drift

- **WHEN** the observed head, proof, branch role, candidate, accepted root, or
  worktree cleanliness changes before apply
- **THEN** closeout SHALL fail before `main` moves.

#### Scenario: Direct main update is attempted

- **WHEN** direct Git mutation bypasses ETHOS
- **THEN** admission SHALL fail closed and the protected ref SHALL remain
  unchanged.

#### Scenario: A second product-owned transition is proposed

- **WHEN** Proxy code attempts to implement the same local `dev` to `main`
  mutation
- **THEN** repository review SHALL reject it as a duplicate lifecycle owner.

### Requirement: Portable product and repository UX

Users SHALL operate the installed `openai-responses-proxy` command for status,
lifecycle, and diagnostics. It SHALL not require Python, module execution, a
source checkout, uv, Nox, ETHOS, repository-specific shell variables, personal
paths, identities, or Forge coupling. Repository verification SHALL expose one
documented locked command graph as a distinct developer surface.

#### Scenario: Operator installs a release

- **WHEN** an operator invokes the documented product installation command
- **THEN** the command accepts explicit release metadata or a resolved manifest without personal paths, identities, or Forge coupling

#### Scenario: Contributor verifies a checkout

- **WHEN** a contributor runs the documented local gate
- **THEN** the same `uv`/`nox` graph is used by CI and ETHOS proof without a second test runner or compatibility wrapper

#### Scenario: A user installs a signed release

- **WHEN** installation completes from a verified asset
- **THEN** status, lifecycle, and diagnostics are available through the product command
- **AND** no `python -m`, source checkout, uv, Nox, or ETHOS command is required at runtime.

### Requirement: Semantic documentation architecture

Proxy SHALL use the resolved official OpenSpec workflow artifacts as the sole
tracked authority for product change intent and SHALL organize its small
canonical documentation kernel by semantic domain.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** ETHOS SHALL derive a
  transient Commitment containing only `schema_version`, `id`, and `acceptance`
  from the selected OpenSpec projection when governance evaluation requires it.
  An additional tracked carrier SHALL exist only when it owns a current invariant
  that official OpenSpec artifacts and existing authorities cannot represent, has
  one named owner and current consumer, replaces rather than parallels another
  authority, and defines its retirement condition. Content document filenames
  SHALL state their subjects. Repository checks and release metadata SHALL
  consume those semantic paths directly.

#### Scenario: Reader enters the documentation

- **WHEN** a reader starts at `docs/README.md`
- **THEN** every canonical document SHALL be reachable through the domain map
- **AND** no redirect-only local index SHALL be required.

#### Scenario: A content-bearing register or policy is stored

- **WHEN** a document owns Decision Record registration or evidence policy
- **THEN** its filename SHALL identify that subject
- **AND** no container-named compatibility copy SHALL remain.

#### Scenario: Repository tooling consumes documentation paths

- **WHEN** quality or release validation reads a canonical document
- **THEN** it SHALL use the same semantic path exposed to readers
- **AND** the documentation tree and executable contract SHALL not diverge.

#### Scenario: Official OpenSpec artifacts carry the intent

- **WHEN** proposal, specification, design, tasks, metadata, configuration, or
  Git history already carry all current meaning for a Change
- **THEN** the repository SHALL retain no additional summary, scope inventory,
  capability descriptor, empty index, or equivalent parallel carrier.

#### Scenario: An additional carrier is necessary

- **WHEN** an invariant cannot be represented by official OpenSpec artifacts or
  an existing authority
- **THEN** the carrier SHALL identify its unique invariant, owner, current
  consumer, replaced authority, and retirement condition
- **AND** validation SHALL reject it if any fact is absent or unverifiable.

#### Scenario: Governance evaluates change intent

- **WHEN** ETHOS evaluates the selected OpenSpec change
- **THEN** it SHALL compile the Commitment transiently from official OpenSpec
  artifacts
- **AND** the repository SHALL persist no parallel Commitment carrier.

#### Scenario: Historical change evidence is inspected

- **WHEN** a maintainer inspects an archived change
- **THEN** official OpenSpec archives and Git history SHALL describe the tracked
  change
- **AND** ETHOS Attestations SHALL remain the effect-evidence surface.

### Requirement: Portable repository quality surface

Repository tooling SHALL represent Forge publication as exact projection of
existing local Git objects. Provider-specific history reconstruction, identity
rewriting, continuity maps, and replay receipts SHALL NOT remain as parallel
publication authorities.

#### Scenario: A maintainer traces branch publication

- **WHEN** the selected Forge branch is advanced
- **THEN** one projector verifies the local object and exact remote CAS coordinate
- **AND** pushes that object without creating another commit
- **AND** no history-mapping module or compatibility flag is consulted.

### Requirement: Tracked content holds no peer coordinates

The changelog and the publication declaration SHALL name no publication peer's
repository, and no tracked content SHALL name the private peer's host, port,
namespace, project path or project ID. Each peer SHALL be resolved from the
clone's Git configuration.

#### Scenario: A test needs a GitLab project

- **WHEN** a test exercises GitLab publication
- **THEN** it SHALL use a fixture host and project ID
- **AND** it SHALL NOT contain the operator's private peer identity.

#### Scenario: A release is recorded in the changelog

- **WHEN** CHANGELOG records a release
- **THEN** it SHALL contain no comparison link or other peer URL.
