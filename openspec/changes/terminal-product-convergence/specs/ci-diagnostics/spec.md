## MODIFIED Requirements

### Requirement: Python compatibility and native release prove distinct facts

Each supported Python minor line SHALL build and install the wheel, then run
the complete non-native behavior inventory. The release session SHALL be the
only native executable build owner and SHALL black-box test the
target-platform executable through one portable process-environment contract.
That contract SHALL preserve the native host execution substrate, redirect
Proxy-owned user, payload, state, and command roots to test-owned locations,
and make Python undiscoverable through the product `PATH`.

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

GitLab verification MUST start from an immutable image containing the
repository-selected UV and Python runtime. It MUST reuse one target-platform
cache for locked package artifacts and UV-managed Python runtimes, and MUST NOT
reinstall UV, download the job's primary Python runtime, or select a different
Python after synchronizing the locked environment. `uv.lock` remains the sole
authority for project and quality dependencies. The CUE CI model SHALL own the
supported Python minor lines and generate `.python-versions` along with both
Forge workflows. Nox and the GitHub matrix reader SHALL consume that projection.

#### Scenario: A fresh GitLab job starts verification

- **WHEN** an admitted GitLab runner starts a verification job with an empty
  project workspace
- **THEN** the selected digest-pinned image already provides UV and the job's
  primary supported Python runtime
- **AND** the job installs repository dependencies from `uv.lock`
- **AND** it does not bootstrap UV through pip or download that primary Python
  runtime before invoking the repository-owned Nox graph
- **AND** subsequent no-sync commands use the synchronized Python identity.

#### Scenario: A runner cache is empty or unavailable

- **WHEN** no reusable dependency cache is present
- **THEN** the job remains correct from the immutable image and committed lock
- **AND** cache absence can affect duration but not dependency authority or
  verification semantics.

#### Scenario: The supported Python matrix executes

- **WHEN** GitLab runs all Python compatibility sessions
- **THEN** the CUE declaration is the only supported-version inventory
- **AND** `.python-versions` is its tracked text projection for native consumers
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
