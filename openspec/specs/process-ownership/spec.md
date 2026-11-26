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

### Requirement: Native resource ownership is exact and symmetric

Creation, observation, transition, and teardown of a native service or process
SHALL consume one exact service identity, executable identity, process
generation, installation root, transaction, and platform carrier. Successful,
failed, timed-out, and interrupted test paths SHALL release every resource they
created and SHALL preserve unrelated and canonical installations.

#### Scenario: A native lifecycle test exits by any path

- **WHEN** the test succeeds, fails an assertion, raises an exception, times out,
  or is interrupted
- **THEN** teardown addresses only the exact test-owned service, process
  generations, transaction, payload, command projection, and platform carrier
- **AND** the native host contains no net test-owned resource growth.

#### Scenario: Ownership cannot be proved

- **WHEN** a service, process, file, or registration matches only a prefix or
  historical convention
- **THEN** cleanup preserves it and reports the missing identity proof
- **AND** no broad prefix deletion or process-name termination is attempted.

### Requirement: Unproved process exit retains its exact failure phase

The process owner SHALL distinguish capture, generation, status, signal, wait,
and post-timeout observation failures. It SHALL retain a safe native reason
without emitting private exception text. A deadline or unreadable observation
SHALL NOT establish a surviving process or authorize payload disposal. Unknown
predecessor exit SHALL preserve the deployment transaction for recovery without
extending deadlines or weakening identity admission.

#### Scenario: Native observation or signalling fails

- **WHEN** a captured process cannot be inspected or signalled
- **THEN** the lifecycle error identifies its failing phase and safe reason
- **AND** a generation mismatch or pre-signal denial sends no signal.

#### Scenario: Exit remains unproved after the deadline

- **WHEN** waiting expires and the same generation's disappearance or zombie
  state cannot be proved
- **THEN** the result reports wait or post-timeout observation as unproved
- **AND** the deployment preserves its transaction and payload for recovery
- **AND** it does not claim that the process is still executing.

#### Scenario: The first test-owned process has an unproved exit

- **WHEN** test teardown cannot prove the first captured process exited
- **THEN** it retains that safe error and still handles independent owned
  processes
- **AND** it fails without deleting the corresponding payload or the outer
  native test root.

### Requirement: Native payload disposal revalidates each owned file

After native-service absence and owned-process exit are proved, the existing
purge owner SHALL revalidate the exact regular-file identity, no-follow
ancestry, and recorded payload digest of each manifest-owned file immediately
before deleting it. It SHALL NOT change permissions, terminate an unproved
holder, retry a failed deletion, or remove unknown content. A failed deletion
SHALL retain the native error and recovery journal.

#### Scenario: An owned file is replaced before its deletion

- **WHEN** the file, its ancestor, or its recorded bytes are replaced after the
  purge captured them
- **THEN** disposal fails with the applicable identity error
- **AND** replacement content and the recovery journal remain protected.

#### Scenario: A native deletion fails

- **WHEN** deletion of one manifest-owned file fails with a native error
- **THEN** disposal fails at once with that native error
- **AND** it changes neither file permissions nor unrelated content
- **AND** the recovery journal remains.
