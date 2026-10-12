## ADDED Requirements

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

### Requirement: Native payload disposal revalidates a bounded sharing retry

After native-service absence and owned-process exit are proved, the existing
purge owner SHALL allow a bounded retry for Windows delete access or sharing
errors. Every attempt SHALL revalidate the exact regular-file identity,
no-follow ancestry, and recorded payload digest. It SHALL NOT change
permissions, terminate an unproved holder, or remove unknown content.
Persistent failure SHALL retain the native error and recovery journal.

#### Scenario: A transient Windows file mapping is released

- **WHEN** deletion of one manifest-owned file fails with a native access or
  sharing error and that obstruction clears within the disposal deadline
- **THEN** the existing purge operation revalidates and removes only that file
- **AND** it changes neither file permissions nor unrelated content.

#### Scenario: A sharing retry outlives its authority

- **WHEN** the file, its ancestor, or its recorded bytes are replaced, or the
  native deletion failure persists until the deadline
- **THEN** disposal fails with the applicable identity or native error
- **AND** replacement content and the recovery journal remain protected
- **AND** non-Windows or unrelated native errors are not retried.
