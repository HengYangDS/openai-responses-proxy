## MODIFIED Requirements

### Requirement: Forge runners come from one platform declaration

The CI graph SHALL name each supported platform's runner once for each Forge that runs that
platform, and the Forge projections SHALL take their runner selectors from it. GitHub and GitLab
SHALL each run the Python compatibility job on macOS for every supported Python minor line from
one job declaration, and a failure of either SHALL fail its pipeline.

#### Scenario: A platform's runner changes

- **WHEN** the declaration names a different runner for one platform on one Forge
- **THEN** regenerating the projections changes the runner selector of that
  Forge's jobs for that platform
- **AND** no Forge adapter names the runner of a declared platform itself.

#### Scenario: GitHub verifies Python on macOS

- **WHEN** GitHub runs the product proof
- **THEN** the macOS Python job runs on the declared macOS runner once for each
  supported Python minor line.

#### Scenario: GitLab verifies Python on macOS

- **WHEN** GitLab runs its product jobs for a merge request into `dev` or for
  `dev`
- **THEN** the macOS Python job runs once for each supported Python minor line on
  the runner that the declaration names for that role
- **AND** the job inherits no Linux default image, runner tag or global variable
- **AND** its failure fails the pipeline.

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
- **THEN** the job records which Mise the runner supplies and installs the locked
  tools with it
- **AND** Mise takes UV and the primary Python runtime at the versions of its
  lock from data, cache and state directories inside the project workspace, and
  checks the checksum of each tool that it downloads
- **AND** the job installs repository dependencies from `uv.lock`
- **AND** its cache key names the macOS target and the job's interpreter rather
  than a Linux target.

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
