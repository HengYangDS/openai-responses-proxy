## ADDED Requirements

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

The CI graph SHALL name each supported platform's runner for each Forge once,
and both Forge projections SHALL take their runner selectors from it. GitHub and
GitLab SHALL each run the macOS Python compatibility job for every supported
Python minor line from one job declaration. GitLab's macOS job SHALL override
the Linux default image and runner tags and install its tools through the
locked Mise.

#### Scenario: A platform's runner changes

- **WHEN** the declaration names a different runner for one platform on one Forge
- **THEN** regenerating the projections changes the runner selector of that
  Forge's jobs for that platform
- **AND** no Forge adapter names the runner of a declared platform itself.

#### Scenario: GitHub verifies Python on macOS

- **WHEN** GitHub runs the product proof
- **THEN** the macOS Python job runs on `macos-latest` once for each supported
  Python minor line.

#### Scenario: GitLab verifies Python on macOS

- **WHEN** GitLab runs its product jobs for a merge request or a protected ref
- **THEN** the macOS Python job runs once for each supported Python minor line
  on a runner tagged `macos-latest` and `review` for the merge request, or
  `macos-latest` and `protected` for the protected ref
- **AND** the job names no container image and installs no operating-system
  package.

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

## MODIFIED Requirements

### Requirement: Hosted verification bootstrap is bounded and repository-owned

Hosted verification SHALL use repository-owned locked supply. GitLab SHALL take
the selected UV and primary Python runtime from an immutable image on Linux and
from the locked Mise on macOS; jobs SHALL retain that synchronized identity and
use one target-platform cache.
`uv.lock` SHALL own project and quality dependencies. CUE SHALL own supported
Python minors and generate both Forge workflows and `.config/ci/python-versions`,
consumed by Nox and the GitHub matrix reader.

#### Scenario: A fresh GitLab job starts verification

- **WHEN** an admitted GitLab Linux runner starts a verification job with an
  empty project workspace
- **THEN** the selected digest-pinned image already provides UV and the job's
  primary supported Python runtime
- **AND** the job installs repository dependencies from `uv.lock`
- **AND** it does not bootstrap UV through pip or download that primary Python
  runtime before invoking the repository-owned Nox graph
- **AND** subsequent no-sync commands use the synchronized Python identity.
- **AND** locked packages and UV-managed interpreters reuse one target-platform
  cache.

#### Scenario: A GitLab macOS job starts verification

- **WHEN** an admitted GitLab macOS runner starts the Python job with an empty
  project workspace
- **THEN** Mise installs UV and the primary Python runtime at the versions and
  checksums of its lock
- **AND** the job installs repository dependencies from `uv.lock`
- **AND** its cache key names the macOS target rather than a Linux one.

#### Scenario: A runner cache is empty or unavailable

- **WHEN** no reusable dependency cache is present
- **THEN** the job remains correct from its immutable image or locked Mise and
  the committed lock
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

## REMOVED Requirements

### Requirement: Forge jobs are portable projections

**Reason**: Its Windows verification scenario leaves with Windows support, and OpenSpec keeps every scenario of a modified requirement.

**Migration**: "Forge jobs run on admitted native runners" keeps every other statement and scenario of it.

### Requirement: Hosted fixtures are platform-default independent

**Reason**: Its newline-translation clause and its OpenSSH key scenario served only Windows runners.

**Migration**: "Hosted fixtures do not depend on the runner's default branch" keeps the default-branch rule and its scenario.

### Requirement: Release signing uses one provider-owned key path

**Reason**: Its Windows ACL clause and its Windows scenario leave with Windows support.

**Migration**: "Release signing uses the provider's key path" keeps every other statement and scenario of it.

### Requirement: Native bundle containment uses filesystem identity

**Reason**: Its two scenarios of separator and case rewriting described Windows path canonicalization only.

**Migration**: "Native bundle containment uses resolved paths" keeps the inside and outside rules and their scenarios.

### Requirement: Native handoff fixtures release temporary bundles

**Reason**: Its bounded retry existed for a Windows mapped-module lock, which macOS and Linux do not hold after a process exits.

**Migration**: "Native handoff fixtures remove temporary bundles after process exit" keeps the exit-before-removal and fail-on-error rules.
