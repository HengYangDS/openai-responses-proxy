## ADDED Requirements

### Requirement: A failed handoff request is settled by evidence of the listener's part

After a handoff request has failed, the controller and `recover` SHALL read a listener at rest, idle or showing
a finished handoff, as proof that no handoff of that request is to come only together with evidence of the
listener's part in it: the request was never sent, the listener refused or acknowledged it, its handoff was
seen to begin and end, or the time in which the listener may begin it has run out. A listener SHALL begin a
handoff only within that time.

#### Scenario: A handoff request is never sent

- **WHEN** the controller fails before it opens the connection of its handoff request
- **THEN** it reads that the listener can begin nothing of that request
- **AND** it rolls the payload back without waiting once health shows the captured listener at rest.

#### Scenario: A listener rolls back a handoff that it acknowledged

- **WHEN** a listener acknowledges a handoff request, the handoff fails before its successor commits, and the
  listener's handoff session returns to idle
- **THEN** the controller reads that the handoff began and ended
- **AND** it rolls the payload back without waiting out the handoff deadline.

#### Scenario: An unanswered request is seen to begin and end

- **WHEN** a handoff request ends without the listener's answer, and the controller then reads the request's
  transaction in the listener's handoff session and afterwards an idle session
- **THEN** the controller reads that the handoff began and ended and rolls the payload back
- **AND** a session that names another transaction is no sight of this request.

#### Scenario: A listener refuses a request that reaches its handoff too late

- **WHEN** a handoff request reaches the begin of its handoff later than the request's own timeout after the
  listener accepted the request's connection
- **THEN** the listener refuses the request with an error status
- **AND** its handoff session is unchanged.

#### Scenario: An unanswered request runs out of time

- **WHEN** a handoff request ends without the listener's answer, the request's timeout has passed since a
  health read that followed the request, and health still shows the captured listener at rest
- **THEN** the controller reads that the listener can no longer begin that request and rolls the payload back
- **AND** before that time a listener at rest proves nothing, and a handoff that begins is followed to its
  end.

#### Scenario: Recovery waits out a handoff request that may be pending

- **WHEN** `recover` finds a transaction whose handoff may have been requested and a predecessor at rest
- **THEN** it restores the prior selection only after the longest timeout that a handoff request can carry has
  passed since it first read that predecessor at rest, and the predecessor is still at rest
- **AND** a handoff that begins in that time is followed to its end
- **AND** a transaction that requested no handoff is restored without that wait.

## MODIFIED Requirements

### Requirement: A lifecycle transition hands off or is refused

Upgrade, rollback and reload SHALL replace a running listener only through the
shared-listener handoff. A runtime qualifies when its identity is complete and
it declares both the `selected-generation-handoff` and the
`admission-preserving-handoff` capability. Any other runtime SHALL be refused
before a projection, a service definition or a process changes.

#### Scenario: A runtime declares both handoff capabilities

- **WHEN** a verified runtime declares both capabilities
- **THEN** upgrade, rollback and reload use the shared-listener handoff
- **AND** successor acceptance still requires the exact payload and process
  identity.

#### Scenario: A runtime lacks a handoff capability

- **WHEN** a verified runtime declares one of the capabilities or neither
- **THEN** upgrade and rollback fail with the incompatible-runtime result,
  which names removal before installation, and reload fails
- **AND** no payload projection, service definition or process changes, and no
  transaction remains.

#### Scenario: Runtime identity is incomplete

- **WHEN** listener, payload, protocol, transaction, or process identity is
  absent or inconsistent
- **THEN** no handoff is attempted
- **AND** the operator receives one precise incompatibility result.

#### Scenario: A handoff fails before its successor commits

- **WHEN** an upgrade or rollback handoff fails before its successor commits
- **THEN** the payload is rolled back only after health proves that the
  captured predecessor still owns the listener, admits requests and has no
  handoff in flight, and the controller holds evidence that no handoff of its
  request is to come
- **AND** without that proof the transaction is preserved for recovery as an
  unknown outcome
- **AND** the controller sends the predecessor no request that changes its
  admission.

#### Scenario: A listener refuses a handoff request before beginning it

- **WHEN** a listener answers a handoff request with an error status without
  beginning the handoff
- **THEN** the controller reads that the listener began nothing and rolls the
  payload back without waiting out the handoff deadline
- **AND** this holds for a listener that an earlier handoff created, whose
  health still shows that finished handoff.

#### Scenario: A handoff request ends without the listener's answer

- **WHEN** the controller is interrupted or loses its connection before the
  listener answers the handoff request
- **THEN** a listener at rest, idle or showing its finished handoff, SHALL NOT
  count as proof that nothing began, because the listener may have the request
  and be about to begin
- **AND** a handoff that begins is followed to its end, and otherwise the
  request is settled as "A failed handoff request is settled by evidence of
  the listener's part" requires, or the transaction is preserved for recovery
  as an unknown outcome.
