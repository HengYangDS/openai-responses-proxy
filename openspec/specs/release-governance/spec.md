# release-governance Specification

## Purpose

Define the provider-neutral validation, product identity, release construction,
and independent publication contract from reviewed source through installation
authority.

## Requirements

### Requirement: Dual-Forge releases project one complete signed bundle

The admitted native builder for each supported platform SHALL produce that
platform's asset pair.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The release owner SHALL admit those assets into exactly
  one complete release-bundle identity and sign it once. Each selected Forge
  SHALL publish and re-download the exact same files, and dual-Forge parity SHALL
  require equal complete inventories, bytes, checksum manifest, signature, and
  trust-anchor digest. Provider adapters SHALL only transport and verify the
  bundle. Release-source verification SHALL inspect the annotated tag and its
  target without changing the caller's symbolic ref, `HEAD`, index, or worktree.

#### Scenario: Physical build execution

- **WHEN** native assets are built on different platform executors or one
  executor is hosted by a selected Forge
- **THEN** runner placement SHALL NOT make that Forge a product authority
- **AND** no Forge publication adapter SHALL build, repackage, or sign assets.

#### Scenario: Complete parity

- **WHEN** GitLab and GitHub both publish a release tag
- **THEN** each release SHALL contain every archive and manifest named by the
  supported platform SSOT plus `SHA256SUMS` and `SHA256SUMS.sig`
- **AND** every corresponding byte digest SHALL be identical
- **AND** both releases SHALL report the same product trust-anchor digest.

#### Scenario: Provider publishes the canonical bundle

- **WHEN** a provider adapter publishes a release
- **THEN** it SHALL consume the canonical pre-signed bundle without rebuilding
  or re-signing any file
- **AND** the re-downloaded result SHALL match that bundle byte for byte.

#### Scenario: Release source is verified

- **WHEN** publication verifies an annotated release tag against an expected
  commit in an attached branch checkout
- **THEN** the tag object and dereferenced commit SHALL be validated exactly
- **AND** the symbolic ref, `HEAD`, index, and worktree SHALL remain unchanged.

#### Scenario: Release source identity differs

- **WHEN** the tag is absent, is not annotated, or resolves to a commit other
  than the expected commit
- **THEN** publication SHALL fail closed before provider I/O
- **AND** the caller checkout SHALL remain unchanged.

#### Scenario: Incomplete or independently signed projection

- **WHEN** either Forge omits a platform, changes any file, regenerates a
  different checksum inventory, re-signs with a different identity, or reports
  a different trust anchor
- **THEN** publication parity SHALL fail closed
- **AND** the incomplete release SHALL NOT be installation authority.

#### Scenario: Optional peer unavailable

- **WHEN** one Forge is unavailable
- **THEN** the other Forge MAY publish the unchanged complete bundle
- **AND** the result SHALL be reported as one-sided publication rather than
  dual-Forge parity.

### Requirement: Validation follows authorization and lifecycle state

The repository SHALL expose one provider-neutral CI graph for the checks that
GitLab and GitHub actually schedule.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Developer and Maintainer authorization,
  guarded ref admission, publication, and parity SHALL remain in their existing
  repository-lifecycle owners rather than being redeclared as unconsumed CI
  fields. GitLab and GitHub SHALL project the same required proof nodes from the
  CI graph. A provider projection MAY omit only a native platform whose runner
  capability is explicitly unavailable; that omission SHALL remain visible and
  SHALL NOT be interpreted as product-level platform evidence.

Each supported Python version SHALL have an independently observable test node.
Independent nodes SHALL be schedulable in parallel and one failed version or
platform SHALL be identifiable without inspecting a combined multi-version
job. A Forge SHALL NOT collapse source governance, quality, the supported
Python matrix, native product acceptance, or publication into one aggregate
verification node.

#### Scenario: Ordinary test in an untagged checkout

- **WHEN** the product test suite validates repository metadata outside a
  review or tag pipeline
- **THEN** it SHALL use ordinary provider-neutral validation
- **AND** it SHALL NOT assume that the current release tag is absent.

#### Scenario: Reviewed source change

- **WHEN** an MR or PR evaluates a proposed product commit
- **THEN** source governance, quality, Python 3.12, Python 3.13, Python 3.14,
  and every available native-platform proof SHALL be separate visible nodes
- **AND** the nodes SHALL bind to the exact proposed product commit
- **AND** a proposal branch push SHALL NOT run a duplicate complete proof.

#### Scenario: Proposal changes after review failure

- **WHEN** a Developer pushes a new commit to a proposal with an open MR or PR
- **THEN** the review event SHALL evaluate the new exact head SHA
- **AND** no success attached only to the previous head SHALL satisfy it
- **AND** the ordinary proposal push SHALL NOT create a duplicate complete
  branch pipeline for that same lifecycle context.

#### Scenario: Developer admission to dev

- **WHEN** an exact proposal head has complete review proof on a selected Forge
- **THEN** the repository lifecycle owner SHALL require the reviewed head to
  descend from the current exact `dev` head
- **AND** that owner SHALL advance `dev` only by guarded fast-forward
- **AND** the absorbed proposal ref SHALL be deleted after successful admission
- **AND** squash, rebase, merge-commit, or provider-authored replacement objects
  SHALL NOT be accepted as the reviewed product commit.

#### Scenario: Maintainer admission to dev

- **WHEN** a Maintainer fast-forwards a locally proven commit directly to `dev`
- **THEN** the `dev` pipeline SHALL execute complete product proof for the
  admitted exact commit
- **AND** failed admission SHALL block promotion and be repaired by a new
  forward commit rather than history rewrite.

#### Scenario: Accepted branch projection

- **WHEN** a reviewed commit is absorbed into `dev`
- **THEN** the branch pipeline SHALL confirm exact accepted-source identity and
  release metadata
- **AND** it SHALL execute the declared accepted-branch proof without consulting
  an undeclared cross-pipeline cache.

#### Scenario: Promotion from dev to main

- **WHEN** a Maintainer opens or updates a `dev` to `main` MR or PR
- **THEN** the promotion proof SHALL bind the exact `dev` head and exact `main`
  base
- **AND** it SHALL require `main` to be an ancestor of `dev`
- **AND** it SHALL require current accepted `dev` proof and release readiness
- **AND** the repository lifecycle owner SHALL advance `main` only by guarded
  fast-forward of the reviewed `dev` commit.

#### Scenario: Promotion head or base changes

- **WHEN** `dev` changes while promotion is open, or the exact `main` base
  changes before admission
- **THEN** the previous promotion proof SHALL become stale
- **AND** the new head/base pair SHALL be evaluated before `main` changes.

#### Scenario: Accepted main projection

- **WHEN** the reviewed `dev` commit is admitted to `main`
- **THEN** the branch pipeline SHALL confirm source identity and release
  metadata for the exact accepted commit
- **AND** the repository lifecycle owner SHALL retain responsibility for the
  guarded ref transition and promotion authorization.

#### Scenario: Direct main mutation

- **WHEN** an actor attempts to update `main` without current promotion proof
- **THEN** the normal lifecycle SHALL reject the transition
- **AND** an emergency exception SHALL require explicit auditable break-glass
  authorization and the complete missing proof rather than an implicit
  Maintainer bypass.

#### Scenario: Pre-tag preparation

- **WHEN** the release owner prepares a new immutable tag
- **THEN** `--prepare-release` SHALL require the current version tag to be
  absent.

#### Scenario: Tagged release checkout

- **WHEN** a Forge verification projection evaluates an existing release tag
- **THEN** it SHALL verify the exact annotated tag and source identity
- **AND** it SHALL build or consume every supported native platform asset
- **AND** the release owner SHALL pass the resulting complete pre-signed bundle
  to the selected provider adapter
- **AND** that adapter SHALL verify its provider-local publication and
  re-downloaded bytes
- **AND** neither the projection nor the adapter SHALL substitute a successful
  peer pipeline for provider-local publication proof.

#### Scenario: Dual-Forge admission race

- **WHEN** both selected Forges are prepared to advance an accepted ref
- **THEN** each update SHALL compare the observed target head and fast-forward
  the same product commit
- **AND** a transport failure MAY retry the unchanged object
- **AND** a genuine target advance SHALL invalidate stale proof and block
  publication until a new common product commit is proven.

#### Scenario: Reduced authority topology

- **WHEN** the repository operates with one selected Forge or with no remote
- **THEN** the same lifecycle invariants SHALL apply to the available authority
  planes
- **AND** the result SHALL state its exact local or one-Forge scope
- **AND** it SHALL NOT claim absent dual-Forge evidence.

#### Scenario: Forge verifies the product tag

- **WHEN** a Forge tag pipeline verifies the published product object
- **THEN** it SHALL supply only the repository, exact tag, and external trust
  anchor
- **AND** it SHALL NOT add a Forge identity to the verifier grammar.

#### Scenario: Platform runner is unavailable on one Forge

- **WHEN** a selected Forge has no admitted runner for a supported native
  platform
- **THEN** its projection SHALL expose that capability gap in the declarative
  topology
- **AND** another Forge's native-platform proof MAY establish product support
- **AND** the selected provider adapter SHALL still verify the complete
  immutable bundle before claiming provider-local publication.

### Requirement: CI projects the complete repository quality contract

The repository SHALL own one explicit quality graph whose concerns cover every
tracked carrier and whose commands are composed once in repository-owned Nox
sessions.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** Local development, repository hooks, GitHub, and GitLab SHALL invoke
  those sessions as projections rather than duplicate their command bodies. CUE
  SHALL validate the semantic equivalence of both Forge projections while allowing
  only runner-native setup and capability-specific platform differences. A green
  subset, repeated equivalent jobs, or success on one Forge SHALL NOT be
  represented as complete repository quality.

#### Scenario: A proposal revision is pushed

- **WHEN** either Forge receives a new proposal commit
- **THEN** source governance, Python quality, each supported Python runtime, and applicable platform/release checks execute for that exact commit
- **AND** a previous green commit cannot satisfy the revised proposal.

#### Scenario: A maintainer advances an accepted branch

- **WHEN** `dev` or `main` advances by an authorized maintainer path
- **THEN** the same repository-owned graph executes for the resulting exact commit
- **AND** direct fast-forward authority does not bypass product proof.

#### Scenario: Forge projections are compared

- **WHEN** repository governance renders or validates GitHub and GitLab CI
- **THEN** both projections contain the same named semantic gates and consume the same Nox owners
- **AND** provider YAML contains no independent quality-policy implementation.

#### Scenario: A tag is evaluated

- **WHEN** an annotated release tag is proposed
- **THEN** the tag pipeline proves source identity, the complete quality graph, supported runtimes, native assets, and release metadata for the tagged commit
- **AND** no branch result or other Forge result substitutes for the tag's own evidence.

#### Scenario: Python quality is evaluated

- **WHEN** repository Python is admitted
- **THEN** formatting, correctness, modernization, imports, typing, naming, exceptions, logging, subprocess safety, security-sensitive execution, pytest idioms, complexity, and performance-smell rules SHALL be evaluated
- **AND** type diagnostics and warnings SHALL fail the gate
- **AND** source or test suppressions SHALL NOT be used to satisfy newly admitted rules.

#### Scenario: Non-Python carriers are evaluated

- **WHEN** repository quality executes
- **THEN** TOML, YAML, JSON/schema, Markdown, prose, links, workflow syntax, secrets, OpenSpec, generated projections, semantic names, commit subjects, decision records, dependency direction, dependency hygiene, package build, installed artifact behavior, and text-byte invariants SHALL each have an explicit owner or an explicit product-irrelevance decision
- **AND** each admitted concern SHALL run over its complete declared inventory.

#### Scenario: A stricter rule is proposed

- **WHEN** a rule family or threshold is added or tightened
- **THEN** its defect class, scope, false-positive cost, remediation, and review condition SHALL be declared
- **AND** existing findings SHALL be repaired or the rule SHALL remain visibly pending
- **AND** copying another repository's number or configuration SHALL NOT itself establish suitability.

### Requirement: Product semantics have one minimal implementation

Every authoritative fact and effect SHALL have one semantic owner. Modules,
commands, adapters, policies, projections, and compatibility surfaces SHALL be
retained only when they carry unique product value. Shared abstractions SHALL
correspond to a stable variation axis rather than textual similarity.

#### Scenario: Parallel or redundant implementation is found

- **WHEN** two carriers own the same fact or behavior, or one carrier has no
  current consumer
- **THEN** the terminal disposition SHALL be absorb, precise rename, semantic
  split, or deletion
- **AND** a forwarding facade, copied policy, negative blocklist, or dormant
  compatibility path SHALL NOT remain after callers migrate.

#### Scenario: Shared abstraction is proposed

- **WHEN** common behavior appears in multiple places
- **THEN** extraction SHALL require a demonstrated stable invariant and real
  consumers
- **AND** dependency direction and public surface SHALL remain narrower after
  extraction
- **AND** no abstraction SHALL be introduced solely to reduce line similarity.

### Requirement: Performance claims are reproducible and budgeted

The product SHALL publish deterministic local benchmarks and reviewed budgets
for request-path and lifecycle overhead. Performance acceptance SHALL use
machine-readable repeated measurements isolated from third-party network
latency. A passing functional suite SHALL NOT be described as performance
optimization evidence.

#### Scenario: Proxy data path is measured

- **WHEN** performance acceptance runs
- **THEN** startup readiness, non-streaming overhead, streaming first-event
  overhead, sustained forwarding, large-payload memory, route resolution, and
  protocol transformation SHALL be measured over declared payload sizes
- **AND** results SHALL include repeated samples and percentile or distribution
  evidence
- **AND** the benchmark upstream SHALL be deterministic and local.

#### Scenario: Performance regresses

- **WHEN** a measured metric exceeds its reviewed budget or allowed baseline
  delta
- **THEN** the gate SHALL fail with the exact metric and comparison
- **AND** profiling SHALL identify the bottleneck before implementation changes
- **AND** correctness, security, portability, and lifecycle guarantees SHALL not
  be weakened to recover the number.

### Requirement: Hosted evidence has one composition boundary

Each provider adapter SHALL validate its Forge-specific required jobs before
returning hosted evidence. The publication orchestrator SHALL normalize that
validated evidence into one closed provider-neutral evaluator schema; the
evaluator SHALL reject unknown fields rather than accept parallel provider
shapes.

#### Scenario: Adapter-shaped evidence reaches the evaluator

- **WHEN** GitHub and GitLab adapters have validated their complete required-job sets
- **THEN** the orchestrator passes only the canonical CI identity fields to the evaluator
- **AND** the resulting evidence preserves the provider adapters' successful verdict
- **AND** unknown evaluator fields still fail closed.

### Requirement: GitLab publication preserves credential semantics

The publication composition root SHALL require one declared GitLab credential
kind and SHALL map a CI job token to `JOB-TOKEN` and a personal, project, or
group access token to `PRIVATE-TOKEN`. It SHALL read only the environment
variable owned by the declared kind and SHALL NOT guess or fall through to a
different credential kind.

#### Scenario: A maintainer publishes outside GitLab CI

- **WHEN** the maintainer selects `private-token`
- **THEN** publication reads the product-scoped private-token variable
- **AND** every GitLab request uses `PRIVATE-TOKEN`
- **AND** an available `CI_JOB_TOKEN` cannot silently replace the selected credential.

### Requirement: Forge publication is restartable without duplicate remote state

Each Forge publication adapter SHALL treat an existing immutable Release and
its exact asset bytes as reusable state. A retry SHALL upload only missing
assets, SHALL reject differing existing bytes or Release identity, and SHALL
preserve bounded provider diagnostics when transport or API validation fails.

#### Scenario: Retry after complete GitLab publication

- **WHEN** the exact tag, Release identity, and complete signed asset bundle
  already exist on GitLab
- **THEN** publication SHALL verify the existing records without uploading a
  second package-file record
- **AND** it SHALL report the publication as matched.

#### Scenario: Retry after partial GitLab asset upload

- **WHEN** some exact assets exist but no Release record has been created
- **THEN** publication SHALL reuse byte-identical assets
- **AND** it SHALL upload only missing assets before creating the Release.

#### Scenario: Existing remote state differs

- **WHEN** an existing asset byte sequence or Release identity differs from the
  requested immutable publication
- **THEN** publication SHALL fail closed without replacing that state.

#### Scenario: GitLab rejects a request

- **WHEN** GitLab returns an unsuccessful HTTP response
- **THEN** the failure SHALL identify the HTTP status and bounded provider
  response detail without exposing credentials.

### Requirement: Accepted corrections advance through immutable SemVer releases

Accepted source that is absent from the latest published release SHALL receive
a new Semantic Versioning identity and immutable signed tag. `VERSION` SHALL
remain the sole version authority, `CHANGELOG.md` SHALL record the forward
release history, and previously published provenance SHALL NOT be rewritten.

#### Scenario: Backward-compatible defect correction is ready for release

- **WHEN** accepted source corrects a defect without changing the public
  compatibility contract and is absent from the latest published release
- **THEN** the release owner SHALL advance the patch version
- **AND** the new signed tag and release bundle SHALL bind that exact accepted
  source.

#### Scenario: Earlier release provenance already exists

- **WHEN** a correction follows an existing published version
- **THEN** the correction SHALL use a later SemVer identity
- **AND** the earlier tag, release record, and assets SHALL remain unchanged.

### Requirement: One declared release age fills every resolver

The repository SHALL declare its release age once, in whole hours, as the release-age setting of its tracked tool
configuration. Its fix command SHALL fill the dependency proposal policy's age and each package manager's own age
setting from that declaration, and its check SHALL fail while one of them differs or another setting holds or
admits releases. A lock that a package manager checks against its age SHALL be refreshed in the change that changes
the age.

#### Scenario: The declared age changes

- **WHEN** the declared age changes and the fix command runs
- **THEN** the proposal policy's root age and uv's setting SHALL hold that number of hours, and npm's setting the
  whole days that cover it
- **AND** the security-fix rule's age SHALL stay zero
- **AND** the check SHALL pass, and a second run of the fix command SHALL write nothing.

#### Scenario: The declared age is zero

- **WHEN** the declared age is zero and the fix command runs
- **THEN** the proposal policy SHALL hold zero hours, and uv's and npm's settings SHALL hold no age.

#### Scenario: A filled age is edited

- **WHEN** a resolver's setting holds another age than the declaration gives
- **THEN** the check SHALL fail, naming that file and the fix command.

#### Scenario: The declaration is not whole hours

- **WHEN** the declaration is absent or written in another unit
- **THEN** the fix command and the check SHALL both refuse, naming the declaration
- **AND** no resolver's file SHALL be written.

#### Scenario: The age changes and a lock is checked against it

- **WHEN** the declared age changes
- **THEN** the same change SHALL carry uv's refreshed lock
- **AND** a package that only the lock pins and that is younger than the age SHALL give way to the newest release
  that has passed it
- **AND** a release that the manifest pins exactly and that is younger than the age SHALL NOT be taken by the
  refresh until it has aged or is exempted as a security fix.

#### Scenario: Another age rule is written

- **WHEN** a package manager's configuration holds another setting that holds or admits releases, other than uv's
  per-package exemption for a security fix
- **THEN** the check SHALL fail, naming the file and the setting.

### Requirement: The repository selects the package manager of its Node tools

The repository's tracked tool configuration SHALL select the npm that installs and audits its locked Node tools, at
an exact version locked by integrity, and every hosted job that installs those tools SHALL run that npm, not the one
that the Node runtime bundles. The Node tools themselves SHALL stay owned by the repository's npm manifest and lock.

#### Scenario: A hosted job installs the Node tools

- **WHEN** a job of either Forge installs and audits the locked Node tools
- **THEN** the npm that runs SHALL be the version that the tool configuration selects

#### Scenario: The package manager is selected

- **WHEN** the tool configuration selects npm
- **THEN** it SHALL name one exact version with an integrity-locked entry, and no Node tool of the repository SHALL be
  selected through the tool configuration instead of the npm lock

#### Scenario: Another npm is on PATH

- **WHEN** the repository's checks run and the npm on PATH is not the version that the tool configuration selects
- **THEN** they SHALL fail before any Node tool runs, naming both versions
