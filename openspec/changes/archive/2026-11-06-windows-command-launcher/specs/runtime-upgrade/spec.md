## MODIFIED Requirements

### Requirement: Source-side upgrade authority

Only the signed-asset installer SHALL admit a different release.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The payload
  transaction SHALL verify and prewarm the exact committed successor executable
  inside the rollback domain before requesting handoff. Handoff readiness SHALL
  use the configured bounded installation deadline without an independent,
  shorter startup cap. Installed control SHALL observe, reload, recover, or remove
  the current product but SHALL NOT accept arbitrary release bytes. Forge
  availability SHALL NOT be an installation input. The payload transaction SHALL
  coordinate the selected serving payload, installed-state record, and native
  command as one rollback domain. The selector SHALL determine the active
  serving generation and its sole predecessor. The command SHALL resolve to the
  newest verified release among those selected generations so serving rollback
  cannot downgrade lifecycle control. The command SHALL be a symbolic link on
  POSIX and a `.cmd` launcher on Windows; each form SHALL be admitted only when it
  identifies that exact control executable.

#### Scenario: An operator installs a release

- **WHEN** one signed native archive and its external trust anchor are supplied
- **THEN** the installer verifies and applies the release locally
- **AND** a Forge, Git, Python, uv, Nox, ETHOS, a client control plane, and a
  source checkout are not runtime dependencies.

#### Scenario: A fresh installation fails after payload projection

- **WHEN** payload bytes and the platform-native command are projected
- **AND** native service startup fails
- **THEN** rollback removes the candidate payload and command
- **AND** the pre-install absence is restored exactly.

#### Scenario: An upgrade handoff fails

- **WHEN** an existing release is upgraded and successor proof fails
- **THEN** rollback restores the prior serving payload and stable command
  ownership exactly
- **AND** foreign content remains unchanged.

#### Scenario: Windows projects the user command

- **WHEN** installation runs on Windows
- **THEN** the command path is a `.cmd` launcher whose exact bytes invoke the
  exact control executable
- **AND** status, rollback, and uninstall classify ownership by those exact bytes
- **AND** no symbolic-link privilege or copied executable is required.

#### Scenario: A cold native successor starts within the configured deadline

- **WHEN** the committed successor needs more than ten seconds for its first start
- **AND** it returns `READY` within the configured installation deadline
- **THEN** the upgrade continues to exact successor identity proof
- **AND** an arbitrary transport cap does not force rollback.

#### Scenario: Exact successor prewarm fails

- **WHEN** the executable committed to the candidate projection fails its bounded probe
- **THEN** the transaction restores the prior projection and command ownership
- **AND** the current verified listener remains available.
