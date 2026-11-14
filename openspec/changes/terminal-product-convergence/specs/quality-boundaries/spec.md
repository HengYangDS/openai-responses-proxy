## ADDED Requirements

### Requirement: Logical and physical topology are one positive model

The repository SHALL positively declare one role for every tracked path. Each
role SHALL state its owner, consumer, source of truth, change condition,
dependency direction, and retirement condition. A path that no role declares
and a path that more than one role matches SHALL fail admission without a
historical forbidden-name list.

#### Scenario: A repository entity is added or moved

- **WHEN** repository quality evaluates the candidate tree
- **THEN** the entity resolves to exactly one declared role
- **AND** its file name conforms to its carrier's name grammar
- **AND** Python imports conform to the declared package architecture.

#### Scenario: A role is declared without its lifecycle

- **WHEN** a role lacks its owner, consumer, source of truth, change condition,
  dependency direction, or retirement condition
- **THEN** the responsibility map fails with the role and the missing field.

### Requirement: Declared quality rules are rational and singly owned

Every quality concern that the responsibility map declares SHALL have one
identity, one owner, one scope, one session and its configuration files, and
SHALL state its risk model, measurement, false-positive cost, remediation, and
review condition. Warnings of tests, Nox sessions and type checks SHALL fail
admission, and a lint exemption SHALL name one exact rule.

#### Scenario: The responsibility map is validated

- **WHEN** the responsibility map is validated
- **THEN** each declared concern has a unique identity and exactly one owner
- **AND** a concern that lacks a rationale field, names an unknown scope, or
  names a configuration file that does not exist fails with its identity.

#### Scenario: A quality rule is reviewed

- **WHEN** maintainers inspect the responsibility map and native tool configuration
- **THEN** the map names the rule's owner, session, scope and configuration
- **AND** a lint exemption that names a prefix family instead of one rule fails.

#### Scenario: A numeric threshold is proposed

- **WHEN** source size, complexity, nesting, parameters, coverage, or performance
  becomes blocking
- **THEN** its concern states the risk model and the measurement in the
  responsibility map.

### Requirement: Source structure has trustworthy bounded admission

Repository quality SHALL enforce one declared control-depth boundary across
product, tooling, test and Nox source. Syntax measurements SHALL preserve sibling
branches, independent scopes, executable decorator lines and semicolon-separated
statements. Native tool policy SHALL own complexity; the shared ETHOS paradigm
SHALL remain the sole file-size threshold owner.

#### Scenario: A control-depth boundary is evaluated

- **WHEN** the repository audit evaluates the selected Python scope
- **THEN** module, class and function bodies are measured independently
- **AND** same-indent elif clauses do not deepen the preceding if block
- **AND** exceeding the declared boundary yields an exact-path nonzero result.

#### Scenario: A syntax count is reviewed

- **WHEN** a comment, docstring, decorator, wrapped expression or semicolon occurs
- **THEN** ELOC excludes only comments and actual documentation spans
- **AND** function ELOC includes its executable decorators
- **AND** logical statement counts follow syntax nodes rather than physical lines.

#### Scenario: A linear native journey remains cohesive

- **WHEN** review finds many statements in one continuous lifecycle journey
- **THEN** its identity, transitions, assertions and teardown remain together
- **AND** a statement ceiling requires demonstrated benefit, not a passing maximum
- **AND** complexity, control depth and the shared file-size gate still apply.
