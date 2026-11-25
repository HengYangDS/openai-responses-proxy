# process-ownership Specification

## Purpose

Define how the proxy proves, captures, and terminates only the exact operating-system process generation that owns a runtime role across handoff and cleanup.

## Requirements

### Requirement: Exited process tombstones are terminal

An exact owned process generation SHALL be considered live only while it can
still execute. A zombie record retained by a non-reaping POSIX parent SHALL be
treated as exited, while PID-reuse protection remains unchanged.

#### Scenario: Container PID 1 retains an adopted zombie

- **WHEN** termination has moved an exact owned generation to zombie state
- **AND** the process table still contains its PID and creation time
- **THEN** bounded termination reports that generation exited
- **AND** liveness observation does not report an orphan
- **AND** payload cleanup may proceed

#### Scenario: The PID belongs to a different generation

- **WHEN** the current process creation time differs from the captured value
- **THEN** termination does not signal the current process
- **AND** the captured generation is not claimed as successfully terminated

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
