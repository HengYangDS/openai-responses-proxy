## MODIFIED Requirements

### Requirement: Python compatibility and native release prove distinct facts

Each supported Python minor SHALL build and install the wheel and run the
complete non-native behavior inventory. Only the release session SHALL build
and black-box test the target-platform executable. One portable child-environment
contract SHALL preserve the native host substrate, isolate Proxy-owned roots,
and exclude Python discovery through the product `PATH`.

#### Scenario: The supported matrix runs

- **WHEN** Python 3.12, 3.13, and 3.14 sessions execute
- **THEN** each tests the installed wheel rather than source-import fallback
- **AND** hosted jobs select minor release lines rather than one host-specific
  patch build
- **AND** platform-specific integration runs only on the platform that owns the
  real system call while synthetic wire fixtures remain portable.

#### Scenario: A native process environment is isolated

- **WHEN** black-box acceptance starts a packaged executable
- **THEN** the child environment derives the execution substrate from the
  current supported native host
- **AND** Proxy-owned user, payload, state, and command roots resolve only
  inside the test-owned workspace
- **AND** the product `PATH` contains no Python executable
- **AND** one platform-neutral contract supplies the environment on macOS,
  Linux, and Windows.

#### Scenario: A native asset is accepted

- **WHEN** the release session packages a supported platform archive
- **THEN** help, version, status, handoff, manifest, and service behavior have
  passed through the built executable under the isolated native environment
- **AND** the archive is bound to the release-owned manifest.

### Requirement: Hosted fixtures are platform-default independent

Repository tests SHALL reach the intended Forge and signing behavior without
depending on a runner's Git default branch or text newline translation.

#### Scenario: Clone already has `main`

- **WHEN** a hosted Git configuration makes `main` the clone's current branch
- **THEN** the tag-pruning fixture still publishes and clones its explicitly
  named `main`
- **AND** continues to test tag pruning rather than fixture setup

#### Scenario: Windows writes a temporary OpenSSH key

- **WHEN** release tests materialize an OpenSSH private key on Windows
- **THEN** the serialized key bytes are preserved exactly
- **AND** `ssh-keygen` can sign the checksum inventory

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

### Requirement: Verification has one repository-owned owner

Nox SHALL own the complete formatting, lint, typing, security, dependency,
documentation-link, architecture, test, release, and platform verification
graph.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The committed uv lock SHALL own Python tool resolution, and project
  metadata SHALL declare the exact current stable uv bootstrap used by local and
  hosted verification. Warnings of tests, Nox sessions and type checks,
  tracebacks, skipped required platforms, and missing runners SHALL NOT be
  represented as success. A pending release heading SHALL match `VERSION` and
  SHALL NOT be dated after the current UTC day before either Forge prepares a
  release.

#### Scenario: A clean checkout is verified

- **WHEN** a contributor or Forge installs the declared uv bootstrap and runs
  the repository command
- **THEN** the lock supplies every runtime and quality dependency
- **AND** Nox resolves the Python matrix from `.config/ci/python-versions`
- **AND** no ambient user site, another repository environment, or unpinned
  package resolution contributes to success.

#### Scenario: Release preparation crosses a UTC date boundary

- **WHEN** a pending version has not been tagged and the current UTC day
  advances past its Changelog date
- **THEN** the prepared heading stays valid without an edit
- **AND** a heading dated after the current UTC day fails closed before proof,
  tagging, or publication.

#### Scenario: Product tags are checked

- **WHEN** local or hosted verification validates release history
- **THEN** it checks the product SemVer tags no newer than the checkout's
  `VERSION`, independently of commit ancestry, and their Changelog headings
  from the exact source checkout
- **AND** tag creation time is not required to equal the Changelog date
- **AND** no Forge name or provider-specific tag namespace enters product metadata.

#### Scenario: GitLab runs a repository Python tool

- **WHEN** a GitLab job invokes release or quality Python code
- **THEN** it runs through `uv run --locked --no-sync`
- **AND** synchronized and executed Python identities are equal
- **AND** no ambient interpreter or uninstalled runtime dependency contributes
  to the result.

#### Scenario: GitLab project coordinates contain namespaces

- **WHEN** release observation queries a GitLab project whose coordinate
  contains `/`
- **THEN** the coordinate is encoded as one GitLab project identifier
- **AND** the API request resolves the intended project rather than a
  truncated path.

#### Scenario: The uv bootstrap advances

- **WHEN** the current stable uv patch is available
- **THEN** project metadata and its executable contract name that same exact version
- **AND** the dependency lock and product runtime semantics remain unchanged.

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

- **WHEN** the GitHub verification workflow reads `.config/ci/python-versions`
- **THEN** it first installs the locked quality environment
- **AND** it invokes the matrix owner through `python -m`
- **AND** no workflow-local parser duplicates the matrix semantics.

#### Scenario: GitLab executes repository release tests

- **WHEN** the GitLab metadata job runs repository-only tests under importlib mode
- **THEN** it invokes pytest through the selected interpreter
- **AND** repository `tools` modules remain importable without `PYTHONPATH`
  injection or a compatibility package.

## ADDED Requirements

### Requirement: Clean-room verification follows the locked repository environment

The repository SHALL expose one cross-platform developer entrypoint that
selects locked tools, reconstructs Work-Lane-local mutable environments, and
runs the same semantic verification graph consumed by both Forges. Ambient
interpreters, user-site packages, global tool configuration, another checkout's
environment, and mutable unpinned resolution SHALL NOT contribute to success.

#### Scenario: A fresh checkout is bootstrapped

- **WHEN** the repository has no local virtual environment, Nox environment,
  Node modules, build output, coverage data, or test temporary state
- **THEN** the documented locked bootstrap reconstructs all required state
- **AND** subsequent verification uses only that checkout's mutable environments
  and shared content-addressed caches.

#### Scenario: Operator uv configuration is present

- **WHEN** the developer entrypoint, Nox or a projected project job launches uv
- **THEN** it selects the repository's native configuration explicitly
- **AND** inherited configuration paths and discovery-disable settings cannot
  replace its version and installation policy
- **AND** external source preparation retains the external repository's policy.

### Requirement: Native process acceptance preserves the host substrate

Black-box native acceptance SHALL derive the child environment from the current
supported host, remove inherited Proxy and Python injection state, redirect all
product-owned roots, and make Python undiscoverable through the product `PATH`.
One semantic owner SHALL supply that environment to fixtures, packaged CLI
contracts, and release verification on macOS, Linux, and Windows.

#### Scenario: A packaged executable runs without Python discovery

- **WHEN** help, version, status, prewarm, or another self-contained public
  command runs under native acceptance
- **THEN** the executable cannot resolve Python through `PATH`
- **AND** the operating-system execution substrate remains available
- **AND** no platform allow-list or Windows-only environment exception is used.
