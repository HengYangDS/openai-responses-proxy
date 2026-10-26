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

CUE SHALL own the semantic CI graph, including jobs, dependencies, triggers,
matrices, platform claims, evidence reuse, release admission, and generated
projection inventory. GitHub Actions and GitLab CI SHALL be checked projections
of that graph. Proposal review, proposal update, maintainer fast-forward, `dev`,
`main`, and tag events SHALL each produce or consume exact-revision evidence
without duplicating jobs that prove no additional fact.

#### Scenario: A developer updates a proposal

- **WHEN** the proposal review SHA changes
- **THEN** the required review graph runs for that exact SHA
- **AND** successful merge deletes the unprotected proposal branch.

#### Scenario: A maintainer fast-forwards an admitted commit

- **WHEN** the exact commit already has reusable evidence for the unchanged
  source, locks, platform, and release context
- **THEN** the target consumes that revision-bound evidence
- **AND** any changed proof input triggers only the newly required jobs.

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

## MODIFIED Requirements

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

#### Scenario: Startup readiness is measured on the packaged product

- **WHEN** performance acceptance measures startup readiness
- **THEN** it SHALL launch the native candidate's listener from one private
  installed generation, as the product's supervisor starts it, and time it from
  process creation through the first complete successful health response
- **AND** identity checks and teardown SHALL stay outside the measured interval,
  and every launched process SHALL be proven gone before the result counts.
