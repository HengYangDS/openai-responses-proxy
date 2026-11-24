## ADDED Requirements

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
