## MODIFIED Requirements

### Requirement: Quality policy has explicit owners

The repository SHALL keep tool-native and repository-level quality policy in
one explicit owner per concern, while ETHOS registers executable gates. A gate
that ETHOS runs before the proof is admitted to the host SHALL run static checks
only; every behavior run, the coverage floor included, SHALL belong to a test
gate.

#### Scenario: A quality gate is planned

- **WHEN** ETHOS plans or executes the Proxy quality proof
- **THEN** its Nox command consumes the tracked policy owners
- **AND** `pyproject.toml` does not duplicate lint, test, type, coverage, or
  repository-structure policy.

#### Scenario: A proof runs its readiness tier

- **WHEN** a full proof runs the gates that come before its host ticket
- **THEN** no pytest case runs among them
- **AND** the behavior suite on every supported Python, with the coverage floor
  on the compatibility floor, runs in a test gate inside the ticket.
