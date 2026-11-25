# Verification Diagnostics

## Purpose

Define one repository-owned verification graph whose successful output is
quiet, reproducible, cross-platform, and sufficient to reject incomplete
product or release candidates.

## Requirements

### Requirement: Verification has one repository-owned owner

Nox SHALL own the complete formatting, lint, typing, security, dependency,
documentation-link, architecture, test, release, and platform verification
graph.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The committed uv lock SHALL own Python tool resolution, and project
  metadata SHALL declare the exact current stable uv bootstrap used by local and
  hosted verification. Warnings, tracebacks, skipped required platforms, and
  missing runners SHALL NOT be represented as success. A pending release heading
  SHALL match `VERSION` and the current UTC date before either Forge prepares a
  release.

#### Scenario: A clean checkout is verified

- **WHEN** a contributor or Forge installs the declared uv bootstrap and runs
  the repository command
- **THEN** the lock supplies every runtime and quality dependency
- **AND** Nox resolves the Python matrix from `.python-versions`
- **AND** no ambient user site, another repository environment, or unpinned
  package resolution contributes to success.

#### Scenario: Release preparation crosses a UTC date boundary

- **WHEN** a pending version has not been tagged and its Changelog date is no longer the current UTC date
- **THEN** release preparation fails closed
- **AND** the heading is advanced before proof, tagging, or publication.

#### Scenario: Product tags are checked

- **WHEN** local or hosted verification validates release history
- **THEN** it checks the reachable product SemVer tags and corresponding
  Changelog headings from the exact source checkout
- **AND** tag creation time is not required to equal the Changelog date
- **AND** no Forge name or provider-specific tag namespace enters product metadata.

#### Scenario: GitLab runs a repository Python tool

- **WHEN** a GitLab job invokes release or quality Python code
- **THEN** it runs through `uv run --locked --no-sync`
- **AND** synchronized and executed Python identities are equal
- **AND** no ambient interpreter or uninstalled runtime dependency contributes
  to the result.

#### Scenario: GitLab project coordinates contain namespaces

- **WHEN** runner admission queries a project whose coordinate contains `/`
- **THEN** the coordinate is encoded as one GitLab project identifier
- **AND** the runner API request resolves the intended project rather than a
  truncated path.

#### Scenario: The uv bootstrap advances

- **WHEN** the current stable uv patch is available
- **THEN** project metadata and its executable contract name that same exact version
- **AND** the dependency lock and product runtime semantics remain unchanged.

### Requirement: Successful verification output is pristine

Passing tests, quality checks, builds, and expected operational-failure probes
SHALL emit no unhandled traceback, warning, `socketserver` exception banner,
package-install warning, false success, or private path. Expected peer
disconnects MAY be suppressed only by the fixture that creates them.

#### Scenario: A test leaks a warning or server exception

- **WHEN** the underlying process exits zero but emits an unhandled warning or
  exception banner
- **THEN** the repository-owned gate fails
- **AND** a Forge cannot report that revision as verified.

#### Scenario: A clean gate succeeds

- **WHEN** quick, quality, matrix, or release verification completes
- **THEN** its log ends with one concise receipt
- **AND** the checkout contains no bytecode, coverage, Ruff, or build cache
  created by that gate.

### Requirement: Python compatibility and native release prove distinct facts

Each supported Python minor line SHALL build and install the wheel, then run
the complete non-native behavior inventory. The release session SHALL be the
only native executable build owner and SHALL black-box test the target-platform
executable with Python absent from the product `PATH`.

#### Scenario: The supported matrix runs

- **WHEN** Python 3.12, 3.13, and 3.14 sessions execute
- **THEN** each tests the installed wheel rather than source-import fallback
- **AND** hosted jobs select minor release lines rather than one host-specific
  patch build
- **AND** platform-specific integration runs only on the platform that owns the
  real system call while synthetic wire fixtures remain portable.

#### Scenario: A native asset is accepted

- **WHEN** the release session packages a supported platform archive
- **THEN** help, version, status, handoff, manifest, and service behavior have
  passed through the built executable
- **AND** the archive is bound to the release-owned manifest.

### Requirement: Coverage is strict and host-independent

The complete behavior suite SHALL keep aggregate and every semantic package's
statement and branch ratios above the floor declared by the canonical coverage
policy. The policy SHALL state its risk model, exact measurement,
false-positive cost, remediation path, and review condition. File-level ratios
MAY be reported for diagnosis but SHALL NOT independently block promotion.

#### Scenario: a quality gate succeeds

- **WHEN** coverage is evaluated for the exact candidate tree
- **THEN** aggregate and semantic-package ratios satisfy the canonical policy
- **AND** no duplicated threshold or file-sized ratio changes the verdict.

#### Scenario: A quality gate succeeds

- **WHEN** coverage is reported for the exact candidate tree
- **THEN** every aggregate and semantic-package ratio satisfies the canonical policy
- **AND** no required test is skipped merely because the quality host differs from the modeled platform.

#### Scenario: platform behavior contributes coverage

- **WHEN** a platform branch cannot execute natively on the quality host
- **THEN** explicit semantic inputs exercise the branch without host spoofing
- **AND** no exclusion or CI-only production condition substitutes for behavior.

### Requirement: Hosted product-tool execution uses the locked product environment

Hosted jobs SHALL install the product runtime and locked repository tooling
before executing dependency-bearing repository modules. Repository-only tools
and their tests SHALL run through the selected interpreter's import-safe module
entrypoint.

#### Scenario: Release metadata executes in GitLab

- **WHEN** the GitLab metadata job validates a branch or tag
- **THEN** it installs the complete environment from `uv.lock`
- **AND** the product CLI dependencies are importable.

#### Scenario: Repository quality executes in either Forge

- **WHEN** a hosted job invokes repository quality checks
- **THEN** it uses the package-aware module entrypoint
- **AND** release chronology tests receive complete tag history.

#### Scenario: GitHub resolves the supported Python matrix

- **WHEN** the GitHub verification workflow reads `.python-versions`
- **THEN** it first installs the locked quality environment
- **AND** it invokes the matrix owner through `python -m`
- **AND** no workflow-local parser duplicates the matrix semantics.

#### Scenario: GitLab executes repository release tests

- **WHEN** the GitLab metadata job runs repository-only tests under importlib mode
- **THEN** it invokes pytest through the selected interpreter
- **AND** repository `tools` modules remain importable without `PYTHONPATH`
  injection or a compatibility package.

### Requirement: Supply-chain pins are current and reproducible

Supported runtimes, direct quality and packaging dependencies, hosted
Actions, CI images, and release tools SHALL use current stable releases through
one repository-owned declaration for each ecosystem.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The committed uv lock SHALL
  own transitive closure, hosted Actions SHALL use immutable revisions, and GitLab
  Python images SHALL bind both the supported Python minor and the exact UV
  version declared by project metadata to immutable registry digests. CI SHALL
  consume those authorities rather than duplicate version literals or retain
  obsolete compatibility fallbacks.

#### Scenario: A GitLab Python image is selected

- **WHEN** a GitLab job selects the supported floor or latest Python image
- **THEN** the reference contains the project UV version, supported Python minor, and a SHA-256 digest
- **AND** both versions match their repository-owned declarations
- **AND** tests derive these relations instead of duplicating the concrete pins.

#### Scenario: The selected image contains an unexpected UV executable

- **WHEN** the observed UV version differs from the project requirement
- **THEN** the job fails before dependency synchronization
- **AND** the diagnostic states both the expected and observed versions.

#### Scenario: A GitLab job runs a Python repository tool

- **WHEN** verification or publication invokes Nox, pytest, or a repository module
- **THEN** UV selects the Python executable synchronized for that job
- **AND** the repository-local Python install and cache directories remain in use
- **AND** no ambient interpreter or implicit Python download contributes to success.

#### Scenario: The supply chain advances

- **WHEN** an audited stable dependency, hosted Action release, or CI base image
  supersedes the repository pin
- **THEN** its existing SSOT is updated without adding a parallel version owner
- **AND** lock, workflow, and repository quality contracts pass together.

#### Scenario: A stable transitive dependency advances

- **WHEN** the declared uv resolver selects a newer stable transitive dependency
- **THEN** the repository SHALL update only `uv.lock`
- **AND** a repeated resolution SHALL produce no further diff
- **AND** the complete locked verification graph SHALL pass before integration.

### Requirement: Hosted setup is deterministic and contention-free

GitHub workflows SHALL initialize Git explicitly, and concurrent Python matrix
jobs SHALL use distinct `setup-uv` cache identities.

#### Scenario: Hosted verification runs concurrently

- **WHEN** supported Python versions execute in parallel
- **THEN** each macOS matrix job uses its interpreter as the cache suffix
- **AND** Git resolves `main` without an initialization hint
- **AND** no verification, release, or provenance gate is weakened

### Requirement: Terminal candidate integration is exact and local

A proven work lane SHALL advance the local candidate only through an explicit
compare-and-swap authority bound to the complete accumulated lane delta.

#### Scenario: The candidate remains the observed ancestor

- **WHEN** full proof passes for the clean archived work-lane HEAD
- **THEN** ETHOS SHALL move `candidate/dev` only from the previously observed ref
- **AND** any candidate, Lease, tree, or proof drift SHALL fail closed
- **AND** no remote Forge SHALL be queried or mutated.

### Requirement: Quality policy has explicit owners

The repository SHALL keep tool-native and repository-level quality policy in
one explicit owner per concern, while ETHOS registers executable gates. A gate
that ETHOS runs before the proof is admitted to the host SHALL run static checks
only; every behavior run, the coverage floor included, SHALL belong to a test
gate.

#### Scenario: A quality gate is planned

- **WHEN** ETHOS plans or executes the Proxy quality proof
- **THEN** its Nox command consumes the tracked policy owners
- **AND** `pyproject.toml` does not duplicate lint, test, type, coverage, or
  repository-structure policy.

#### Scenario: A proof runs its readiness tier

- **WHEN** a full proof runs the gates that come before its host ticket
- **THEN** no pytest case runs among them
- **AND** the behavior suite on every supported Python, with the coverage floor
  on the compatibility floor, runs in a test gate inside the ticket.

### Requirement: Commit semantics are machine checked

The repository SHALL admit human commit subjects only through one scoped
Conventional Commit grammar and SHALL classify generated lifecycle commits
through explicit semantic patterns.

#### Scenario: A human commit omits its semantic scope

- **WHEN** repository quality validates its subject
- **THEN** the commit is rejected before publication.

### Requirement: A patch release has one source identity and independent Forge projections

`VERSION`, package metadata, Changelog, documentation, signed tag, and assets
SHALL derive from one accepted local commit. The release tag SHALL be signed
once as one local Git tag object and pushed unchanged to either optional Forge.
Each Forge SHALL run and publish independently without consuming the other.

#### Scenario: Both Forge planes publish the current patch

- **WHEN** local exact-HEAD proof passes and the signed local tag exists
- **THEN** GitLab and GitHub receive the same commit OID and tag object OID
- **AND** each publishes its own CI result, Release record, and complete assets
- **AND** a later read-only audit compares exact identities and payload digests.

#### Scenario: One Forge publication fails

- **WHEN** either Forge cannot publish
- **THEN** the other remains independently publishable and usable
- **AND** no local or peer Git object is rewritten to conceal the failure.

#### Scenario: A release asset is installed

- **WHEN** an operator installs the platform archive for the value in `VERSION`
- **THEN** the installer verifies the complete release set and external trust anchor before mutation
- **AND** the installed executable reports that exact version and passes runtime acceptance.

#### Scenario: Accepted source advances after a release

- **WHEN** an accepted unreleased repair changes the source tree after the
  version in the latest immutable tag
- **THEN** `VERSION` advances to one newer SemVer patch before publication
- **AND** the Changelog records the repair under that same version
- **AND** existing tags, runs, Releases, and assets remain unchanged.

#### Scenario: GitLab publishes from the immutable runtime

- **WHEN** GitLab publishes the current patch
- **THEN** the job uses the repository-declared immutable release runtime
- **AND** it does not install operating-system packages during publication.

### Requirement: Commit grammar follows the checkout's available integration boundary

Commit-subject verification MUST validate the change range after the most local
available integration boundary without requiring local governance refs in a
Forge checkout.

#### Scenario: A Work Lane has a candidate boundary

- **WHEN** `candidate/dev` is available
- **THEN** only commits after that candidate boundary are checked
- **AND** an invalid Work Lane subject is rejected.

#### Scenario: A Forge tag checkout has no candidate ref

- **WHEN** the checkout exposes `origin/dev` or `origin/main` but no `candidate/dev`
- **THEN** the available remote integration ref is used as the boundary
- **AND** an invalid subject after that boundary is rejected
- **AND** no candidate or Work Lane ref is published to satisfy the checker.

#### Scenario: No integration boundary is available

- **WHEN** the repository has Git history but none of the declared integration refs
- **THEN** all available history is checked
- **AND** history unavailability remains a fail-closed diagnostic.

### Requirement: Hosted fixtures own deterministic repository and process identities

Hosted verification MUST create Git repositories with an explicit non-product
initial branch. Native handoff fixtures MUST capture every successor process
generation proven through expected health identity and MUST confirm those exact
generations have exited before their executable payload is removed.

#### Scenario: Host Git configuration differs

- **WHEN** a quality fixture creates an isolated repository
- **THEN** its initial branch is independent of the host's Git defaults
- **AND** no product integration ref is created implicitly.

#### Scenario: Captured process later denies argv access

- **WHEN** expected health proves a native successor and captures its PID,
  executable, and creation time
- **AND** a later argv lookup is unavailable during process exit
- **THEN** teardown terminates the captured PID generation without relying on
  that later argv lookup
- **AND** payload deletion starts only after the generation is absent.

#### Scenario: A captured PID is reused

- **WHEN** the current process creation time differs from the captured value
- **THEN** teardown treats the captured generation as absent
- **AND** does not signal the new process.

#### Scenario: Process inventory omits a serving successor

- **WHEN** a native handoff fixture has captured the successor at bound health
- **THEN** teardown still terminates that exact generation
- **AND** inventory remains only a secondary discovery path.

### Requirement: Publication topology has one declared peer collection

The repository SHALL declare local verification and installation once and
represent every remote publication plane as one peer in the same collection.
Forge-specific scalar aliases SHALL NOT be accepted as parallel configuration
owners.

#### Scenario: GitLab and GitHub are both available

- **WHEN** publication readiness compiles the repository release declaration
- **THEN** GitLab and GitHub appear as independent peers with their own Git remote and CI surface
- **AND** neither peer supplies credentials, jobs, tags, Releases, or assets for the other.

#### Scenario: A retired scalar field returns

- **WHEN** the publication table contains a Forge-specific remote or CI scalar
- **THEN** repository quality rejects the declaration
- **AND** no compatibility reader or inferred default peer is used.

### Requirement: Forge release projections use one exact common runtime

The repository SHALL define one immutable runtime identity for every native
asset built on more than one Forge, while each Forge remains an independent
builder and publisher.

#### Scenario: Independent Linux builders select the release runtime

- **WHEN** GitLab and GitHub build the Linux release asset
- **THEN** both use the repository-owned image digest and architecture
- **AND** both materialize the release commit at the same canonical build root
- **AND** neither builder consumes artifacts or state from the other Forge

### Requirement: Native release payloads are reproducible

Native release payloads SHALL contain only runtime-required bytes and portable
metadata. The release build SHALL remove installer-local metadata and repair
its inventory before native executable freezing. Every platform built by both
Forge planes from the same accepted source and locked toolchain SHALL publish
byte-identical archives, manifests, and checksum entries.

#### Scenario: Equivalent source is built from distinct checkout roots

- **WHEN** the same release commit is installed and built twice in the declared
  runtime with different checkout paths or installer timestamps
- **THEN** the frozen executable inputs and resulting common-platform archives
  are byte-identical
- **AND** no checkout path, installer timestamp, installer cache record, or
  runner-private metadata is present.

#### Scenario: Independently published common assets are compared

- **WHEN** GitLab and GitHub finish publishing the same release version
- **THEN** a read-only audit downloads each common-platform asset and its
  manifest from both independent Forge planes
- **AND** their SHA-256 digests are equal before installation is accepted.

#### Scenario: A builder drifts from the declared runtime

- **WHEN** a build uses a different image, Python patch release, toolchain, or
  non-normalized installed distribution
- **THEN** release verification fails before publication.

### Requirement: Hosted native asset jobs preserve path identity

A hosted native asset job SHALL write each accepted platform bundle to the exact directory consumed by its artifact uploader across every process, container, and host boundary.

#### Scenario: Linux build runs in a job container

- **WHEN** the pinned Linux release container builds and accepts the native asset
- **THEN** the output directory is mounted into both the container and host action
- **AND** the upload action reads that exact directory without path translation
- **AND** GitLab and GitHub continue to build and publish independently

### Requirement: Container and action paths share one mounted workspace

The GitHub Linux release job SHALL write its output through the container's
runtime `GITHUB_WORKSPACE` path. The upload action SHALL read the equivalent
`${{ github.workspace }}` path.

#### Scenario: Linux native asset crosses the container boundary

- **WHEN** the tagged Linux job builds the native release asset
- **THEN** the container writes below `$GITHUB_WORKSPACE/.release-assets`
- **AND** the upload action reads `${{ github.workspace }}/.release-assets`

### Requirement: Dependency-minimal verification

Hosted verification MUST install only the locked tools required to execute its
declared checks. Native release builders MUST remain in a distinct release
group and MUST be installed only by the native release session.

#### Scenario: Quality verification runs without PyInstaller

- **WHEN** a Python quality or release-metadata job prepares its environment
- **THEN** it installs the `quality` dependency group
- **AND** it does not resolve or install PyInstaller
- **AND** a native release build explicitly installs both `quality` and
  `release`.

### Requirement: Platform-true GitLab evidence

Every GitLab job that represents Linux x86_64 behavior MUST execute an
`linux/amd64` container regardless of the runner host architecture.

#### Scenario: Darwin arm64 runner executes Linux verification

- **WHEN** the registered runner schedules a verification or release job
- **THEN** the selected image declares `linux/amd64`
- **AND** the resulting evidence is not labeled from the runner host
  architecture
- **AND** no GitHub workflow, tag, asset, or availability is required.

### Requirement: Hosted verification bootstrap is bounded and repository-owned

Hosted verification SHALL use repository-owned locked supply. GitLab SHALL start
from an immutable image containing the selected UV and primary Python runtime;
jobs SHALL retain that synchronized identity and use one target-platform cache.
`uv.lock` SHALL own project and quality dependencies. CUE SHALL own supported
Python minors and generate both Forge workflows and `.config/ci/python-versions`,
consumed by Nox and the GitHub matrix reader.

#### Scenario: A fresh GitLab job starts verification

- **WHEN** an admitted GitLab runner starts a verification job with an empty
  project workspace
- **THEN** the selected digest-pinned image already provides UV and the job's
  primary supported Python runtime
- **AND** the job installs repository dependencies from `uv.lock`
- **AND** it does not bootstrap UV through pip or download that primary Python
  runtime before invoking the repository-owned Nox graph
- **AND** subsequent no-sync commands use the synchronized Python identity.
- **AND** locked packages and UV-managed interpreters reuse one target-platform
  cache.

#### Scenario: A runner cache is empty or unavailable

- **WHEN** no reusable dependency cache is present
- **THEN** the job remains correct from the immutable image and committed lock
- **AND** cache absence can affect duration but not dependency authority or
  verification semantics.

#### Scenario: The supported Python matrix executes

- **WHEN** GitLab runs all Python compatibility sessions
- **THEN** the CUE declaration is the only supported-version inventory
- **AND** `.config/ci/python-versions` is its tracked text projection for native consumers
- **AND** additional matrix interpreters may be acquired by UV and stored in
  the declared target-platform cache
- **AND** their patch versions and installation paths do not become a second
  repository authority.

#### Scenario: The supported Python declaration changes

- **WHEN** the native projection checker finds a matrix or Forge file that differs
  from the CUE declaration
- **THEN** it refuses without changing any file
- **AND** explicit regeneration updates every declared projection
- **AND** a second generation is byte-identical and unrelated files are preserved.

### Requirement: Dual-Forge lineage compares exact current identity

The parity audit SHALL require local, GitLab, and GitHub current branch OIDs and
release tag object OIDs to be equal. Equal trees or an equal ordered tree suffix
SHALL NOT substitute for exact object identity.

#### Scenario: Provider tips have equal trees but different commits

- **WHEN** GitLab and GitHub point to different commit OIDs with equal trees
- **THEN** parity fails
- **AND** publication remains incomplete until both refs equal the local source OID.

### Requirement: Release publication uses the immutable repository runtime

GitLab release publication SHALL execute in the digest-pinned Linux release
runtime declared by repository metadata. It SHALL NOT install operating-system
packages while publishing a release.

#### Scenario: GitLab publishes a verified release

- **WHEN** the signed tag, source, quality graph, and native asset are complete
- **THEN** publication starts with Python, Git, OpenSSH, curl, binutils, and tar
  available from the immutable repository runtime
- **AND** no Debian package index or package download is required
- **AND** the publisher still executes through the locked synchronized Python
  environment.

### Requirement: Product release metadata is Forge-neutral

The release metadata validator SHALL derive product version, Changelog, and tag
state only from the exact local checkout. Forge selection, transport identity,
and peer-local Release records SHALL remain outside that product semantic.

#### Scenario: Either Forge validates the same source object

- **WHEN** GitLab or GitHub runs release metadata validation for the same commit
- **THEN** both invoke the same provider-free command and observe the same result
- **AND** no provider flag, provider tag namespace, or compatibility alias exists.

### Requirement: Forge jobs run on admitted native runners

Hosted jobs SHALL use supported native shells and filesystem semantics, install
their explicit operating-system prerequisites, and reach a terminal result on
an admitted project runner. A reused self-hosted checkout SHALL preserve Git
diagnostic integrity without changing runner-global configuration. A container
whose user does not own the GitHub checkout SHALL grant Git trust only to the
exact workflow workspace for the one archive command.

#### Scenario: Linux builds an x86_64 asset on an ARM host

- **WHEN** the Docker executor runs on ARM
- **THEN** the release job explicitly selects an amd64 container
- **AND** packaging fails unless the container reports an x86_64-compatible
  machine.

#### Scenario: A required runner is unavailable

- **WHEN** Forge admission cannot match every job to an allowed runner
- **THEN** publication is blocked before a pipeline is treated as accepted
- **AND** a pending job is not reported as verification success.

#### Scenario: A GitHub release container reads the checked-out tag

- **WHEN** the Linux container user does not own `GITHUB_WORKSPACE`
- **THEN** the archive command trusts that exact workspace for that invocation
- **AND** no global, repository-local, system, or wildcard safe-directory rule
  is created
- **AND** the canonical `/workspace` source materialization continues.

### Requirement: Forge runners come from one platform declaration

The CI graph SHALL name each supported platform's runner once for each Forge that runs that
platform, and the Forge projections SHALL take their runner selectors from it. GitHub SHALL run the
Python compatibility job on macOS for every supported Python minor line.

#### Scenario: A platform's runner changes

- **WHEN** the declaration names a different runner for one platform on one Forge
- **THEN** regenerating the projections changes the runner selector of that
  Forge's jobs for that platform
- **AND** no Forge adapter names the runner of a declared platform itself.

#### Scenario: GitHub verifies Python on macOS

- **WHEN** GitHub runs the product proof
- **THEN** the macOS Python job runs on the declared macOS runner once for each
  supported Python minor line.

### Requirement: Hosted fixtures do not depend on the runner's default branch

Repository tests SHALL reach the intended Forge behavior without depending on a
runner's Git default branch.

#### Scenario: Clone already has `main`

- **WHEN** a hosted Git configuration makes `main` the clone's current branch
- **THEN** the tag-pruning fixture still publishes and clones its explicitly
  named `main`
- **AND** continues to test tag pruning rather than fixture setup

### Requirement: Release signing uses the provider's key path

Release signing MUST use a complete caller-provided private-key file without
copying it. It MAY create one process-scoped private copy only to restore a
missing terminal newline. Tests MUST model both contracts.

#### Scenario: A Forge signs release assets

- **WHEN** GitHub or GitLab assembles one release asset set
- **THEN** the protected environment supplies an existing private-key path
- **AND** repository code preserves a complete key's path and security metadata
- **AND** OpenSSH signs and independently verifies the checksum inventory.

#### Scenario: The signing input is unsafe

- **WHEN** the path is absent, a symbolic link, or the trust input is empty
- **THEN** release assembly fails closed before publication.

#### Scenario: The provider supplies a complete key

- **WHEN** the private-key file ends with a terminal newline
- **THEN** OpenSSH receives that exact file path
- **AND** no temporary private-key copy is created.

#### Scenario: A file variable omits its terminal newline

- **WHEN** a valid private-key file lacks its terminal newline
- **THEN** signing uses one process-scoped `0600` normalized copy
- **AND** removes the copy after signing.

### Requirement: Native bundle containment uses resolved paths

Release assembly MUST accept a member that resolves inside the resolved bundle
and MUST reject a member that resolves outside it.

#### Scenario: A symlink resolves outside the bundle

- **WHEN** a member resolves outside the canonical bundle boundary
- **THEN** release assembly fails closed
- **AND** publishes no asset from that invocation

#### Scenario: A bundle contains internal symlinks

- **WHEN** a bundle member is a symlink that resolves inside the bundle
- **THEN** release assembly materializes the link's target at the member's
  logical path
- **AND** rejects links that resolve outside the bundle

### Requirement: Native handoff fixtures remove temporary bundles after process exit

Native release acceptance MUST terminate and re-observe every fixture-owned
proxy process before removing its copied bundle, and MUST fail if that removal
fails.

#### Scenario: Every fixture-owned process has exited

- **WHEN** every fixture-owned proxy process has exited
- **THEN** acceptance removes the copied bundle once
- **AND** starts that removal only after the exit is observed

#### Scenario: Payload cleanup fails

- **WHEN** payload cleanup reports any error
- **THEN** acceptance fails immediately
- **AND** does not hide the residual payload
