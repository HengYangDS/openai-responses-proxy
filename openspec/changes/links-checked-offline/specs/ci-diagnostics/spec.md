## ADDED Requirements

### Requirement: Link checks need no network

The repository's documentation link check SHALL make no network request. The local check and every job of either
Forge that runs the governance check SHALL run the same link check with the same configuration: it checks the links of
the tracked Markdown that the governance check selects, outside archived OpenSpec changes, to local files and
their anchors, and SHALL NOT fetch an external URL. No option, job or
configuration SHALL select a second link-check mode.

#### Scenario: A Forge job and the local check check links

- **WHEN** a job of either Forge or the repository's local check runs the governance check
- **THEN** each SHALL run the same offline link check with the same configuration
- **AND** no command line SHALL select another link-check mode

#### Scenario: A local link or anchor is broken

- **WHEN** a tracked Markdown file that the governance check selects links to a local file or anchor that does not
  exist
- **THEN** the link check SHALL fail on both Forges and in the local check

#### Scenario: An external host answers an error

- **WHEN** an external URL in the tracked Markdown answers 503 or 504, or cannot be reached
- **THEN** the link check result SHALL be unchanged, because no request is sent
