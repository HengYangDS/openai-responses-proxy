## ADDED Requirements

### Requirement: Changelog is a release-history contract

`CHANGELOG.md` SHALL follow Keep a Changelog 1.1.0: one leading `Unreleased`
section, canonical categories, and descending SemVer releases. Within the
selected checkout's `VERSION` horizon, product tags and released sections SHALL
correspond one-to-one, except for the current prepared release. Publication
SHALL bind the exact tag, `VERSION`, first released section, and `HEAD`.

#### Scenario: Development continues between releases

- **WHEN** `VERSION` advances beyond the latest product tag
- **THEN** user-visible work SHALL remain under `Unreleased`
- **AND** ordinary development SHALL NOT manufacture dated release history.

#### Scenario: Unreleased work claims a release date

- **WHEN** the `Unreleased` heading carries a date
- **THEN** changelog validation SHALL fail before release preparation or
  admission.

#### Scenario: Release history and Git disagree

- **WHEN** a product tag within the selected release horizon lacks a released section, a historical released
  section lacks its tag, or the selected tag differs from `VERSION`, the first
  released section, or `HEAD`
- **THEN** release admission SHALL fail before construction or publication.

#### Scenario: A release commit is prepared before its tag

- **WHEN** the current untagged `VERSION` has a dated release section
- **THEN** it SHALL be the only untagged released section and the first section
  after `Unreleased`
- **AND** every older released section SHALL already correspond to a product tag.

#### Scenario: A historical checkout retains newer tags

- **WHEN** a shared clone contains product tags newer than the selected
  checkout's `VERSION`
- **THEN** provenance validation SHALL compare tags no newer than that `VERSION`,
  independently of commit ancestry
- **AND** a missing historical tag SHALL still fail validation
- **AND** release preparation SHALL refuse an existing same-name tag even when
  its target is outside the selected checkout's ancestry.

### Requirement: One CI model covers every integration path

CUE SHALL own both Forge CI definitions: one shared contract for the Python
matrix, the verification sessions and the tool supply, and each Forge's jobs,
dependencies and triggers. GitHub Actions and GitLab CI files SHALL be checked
projections of it. Proposal review, proposal update, `dev`, `main`, and tag
events SHALL each run their required graph for the exact revision.

#### Scenario: A developer updates a proposal

- **WHEN** the proposal review SHA changes
- **THEN** the required review graph runs for that exact SHA
- **AND** successful merge deletes the unprotected proposal branch.

#### Scenario: A maintainer fast-forwards an admitted commit

- **WHEN** an admitted commit reaches `dev` or `main`
- **THEN** the accepted-branch event runs its required graph for that exact
  revision
- **AND** a Forge file that differs from its CUE projection fails the check
  without changing any file.

#### Scenario: A required branch job is skipped

- **WHEN** a `dev` or `main` review or accepted-branch event lacks any job
  required for that event, including a skipped or cancelled job
- **THEN** its stable branch admission check SHALL fail for the exact revision
- **AND** tag, release, and manual verification events SHALL NOT emit that
  required branch check.

#### Scenario: Compatibility consumes a workflow-built candidate

- **WHEN** a workflow verifies upgrade and rollback from a signed published
  predecessor
- **THEN** it consumes the same workflow's native platform asset after checking
  its selected version, platform, complete checksums and internal manifest
- **AND** missing or mismatched candidate input fails without a source rebuild
- **AND** published-release signature admission remains independently required.

### Requirement: Publication closes source, Forge, and branch state

A release SHALL create one signed local commit and annotated tag object, project
those exact objects independently to selected Forges, publish complete matching
asset inventories, and retire merged proposal and delivery refs. GitHub and
GitLab MAY supply different native runner sets, but each projection SHALL state
the facts it proves and SHALL NOT relabel another platform's evidence.

#### Scenario: A release is complete

- **WHEN** both selected Forge publications and installed-product acceptance pass
- **THEN** local and remote `main` and `dev` identify the accepted object
- **AND** merged proposal branches, remote `work/*`, draft releases, and failed
  unpublished intermediates have been removed.

### Requirement: One dependency proposal policy

The repository SHALL keep one Forge-neutral dependency proposal policy that
reads every pinned input from its authored manifest, lock or CUE declaration,
never from a Forge projection. A maintenance run SHALL compute candidates
against one selected Forge, and publication SHALL give the other Forge the same
signed object.

#### Scenario: A pinned input has a newer stable release

- **WHEN** a maintenance run calculates candidates
- **THEN** exactly one rule of the one policy reads every pinned input
- **AND** the run proposes at most one update branch at a time against `dev`.

#### Scenario: A second updater is configured

- **WHEN** a Dependabot configuration, a Forge-discovered Renovate
  configuration, or a Forge job that runs an updater appears
- **THEN** repository tests SHALL fail
- **AND** neither Forge can open a competing update for the same change.

### Requirement: Dependency updates follow release age and kind

The policy SHALL declare one release age, in hours, for every update it
proposes; only a security fix skips it. Only minor and patch updates of stable
dependencies MAY merge automatically; toolchains, Actions, images, pre-1.0 and
major versions SHALL need deliberate admission.

#### Scenario: The release age changes

- **WHEN** the release age is raised or lowered
- **THEN** one value of the policy changes, written as a whole number of hours,
  and every proposed update follows it
- **AND** repository tests SHALL fail while another rule, a preset, a schedule
  or a package manager's own setting holds or admits releases by another rule
- **AND** they SHALL fail while the security rule is disabled, scheduled, or
  without its advisory source.

#### Scenario: A release is younger than the release age

- **WHEN** the release age is above zero and the newest stable release of a
  pinned input is younger than it, and no advisory names the pinned release
- **THEN** the policy proposes the newest release that has passed the age, or
  none
- **AND** a release whose registry publishes no time stays held, so a refresh
  that takes it first reads its age from the publisher's release record.

#### Scenario: A package is locked only as a transitive dependency

- **WHEN** a package is pinned only by a lock, as a dependency of a declared one
- **THEN** the policy proposes no update for it, and a lock refresh moves it
- **AND** that refresh SHALL apply the declared release age itself.

#### Scenario: A security fix is published

- **WHEN** an advisory affects a locked version
- **THEN** its fixed release is proposed without the release age and ahead of
  queued proposals
- **AND** admission still requires signatures, the current-commit tests and the
  Change's approval.

#### Scenario: A major or pre-1.0 update is available

- **WHEN** a candidate is a major version, a pre-1.0 release, a toolchain, an
  Action or an image
- **THEN** the policy does not merge it automatically
- **AND** a Work Lane admits it with its own evidence.

## MODIFIED Requirements

### Requirement: Performance claims are reproducible and budgeted

The product SHALL publish deterministic local benchmarks and reviewed budgets
for request-path overhead, startup readiness and handoff control. Performance
acceptance SHALL use
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

#### Scenario: Startup readiness is measured on the packaged product

- **WHEN** performance acceptance measures startup readiness
- **THEN** it SHALL launch the native candidate's listener from one private
  installed generation, as the product's supervisor starts it, and time it from
  process creation through the first complete successful health response
- **AND** identity checks and teardown SHALL stay outside the measured interval,
  and every launched process SHALL be proven gone before the result counts.
- **AND** each measured run SHALL name the committed payload identity and the
  executable SHA-256 that its samples launched, and verification SHALL refuse a
  run that names another payload or none.
