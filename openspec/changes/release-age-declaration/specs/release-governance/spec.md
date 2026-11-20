## ADDED Requirements

### Requirement: One declared release age fills every resolver

The repository SHALL declare its release age once, in whole hours, as the release-age setting of its tracked tool
configuration. Its fix command SHALL fill the dependency proposal policy's age and each package manager's own age
setting from that declaration, and its check SHALL fail while one of them differs. A lock that a package manager
checks against its age SHALL be refreshed in the change that changes the age.

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
- **AND** a locked release that is younger than the age SHALL give way to the newest release that has passed it.
