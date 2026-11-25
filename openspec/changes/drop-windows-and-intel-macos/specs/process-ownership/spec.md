## ADDED Requirements

### Requirement: Successor capture follows the protocol-proven PID

After handoff health proves the successor PID, transaction, release, payload,
receipt, manifest, serving state, and admission state, cleanup SHALL capture the
same PID generation without depending on a second command-line projection.

#### Scenario: The successor's command line is unreadable

- **WHEN** exact successor health has proved a positive PID
- **AND** the native process command line is inaccessible
- **THEN** cleanup records that PID and its creation time
- **AND** termination signals only that exact generation
- **AND** temporary payload deletion occurs after confirmed exit

#### Scenario: PID has been reused

- **WHEN** a captured PID and creation time are known
- **AND** the current process creation time differs
- **THEN** cleanup does not signal the process

## REMOVED Requirements

### Requirement: protocol-proven successor capture

**Reason**: Its only capture scenario was named for the Windows launcher, and OpenSpec keeps every scenario of a modified requirement.

**Migration**: "Successor capture follows the protocol-proven PID" keeps the capture rule, with an unreadable command line as its scenario.
