# quality-boundaries Specification

## Purpose

Define the repository's positive semantic ownership, dependency direction, and
portable verification boundary without turning descriptive source metrics into
arbitrary merge vetoes.

## Requirements

### Requirement: One structural quality boundary

Every tracked product carrier SHALL belong to exactly one positive quality role,
and every blocking concern SHALL have one semantic owner and a proportionate
evidence model.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** The quality responsibility map SHALL cover product and tool source, tests,
  documentation, structured configuration, dependency and architecture topology,
  security, commits, CI, release construction, and supported-platform behavior.
  Each concern SHALL state its governed scope, risk model, exact measurement,
  false-positive cost, remediation path, and review condition. Files SHALL NOT be
  silently excluded, multiply owned, or admitted by a historical forbidden-item
  list. Tool-native configuration SHALL own syntax-level policy, while custom
  repository code SHALL be limited to cross-file or product-semantic constraints
  that mature tools cannot express.

Aggregate coverage SHALL own the quantitative product-risk boundary. Every
semantic package SHALL have current non-zero execution evidence and an exact
diagnostic ratio; package- and file-sized percentages SHALL NOT act as
independent vetoes. Complexity, source size, nesting, and parameter counts SHALL
be blocking only when the canonical policy supplies a risk-derived threshold;
otherwise they SHALL remain visible observations. The architecture gate SHALL
own the declared product package, semantic package set, dependency direction,
root-module boundary, explicit package-initializer policy, package declarations,
and acyclic dependency graph.

Public product and repository-tool APIs SHALL have complete, signature-consistent
documentation and sound types. Tests SHALL use names and assertions as their
behavior contract and SHALL NOT be required to duplicate that contract in
ornamental docstrings. Formatting, import normalization, correctness,
modernization, typing, naming, exception and logging discipline, subprocess and
security-sensitive execution, pytest idioms, dead code, dependency hygiene,
prose, structured configuration, links, workflow syntax, secrets, commits, and
release metadata SHALL each be evaluated in their applicable role. Inline
suppression, blanket ignores, and checked-in diagnostic baselines SHALL NOT be
the mechanism for passing repository quality.

#### Scenario: A tracked carrier enters the repository

- **WHEN** repository quality evaluates the tracked tree
- **THEN** the carrier is assigned to exactly one declared quality role
- **AND** an uncovered or multiply owned carrier fails with its exact path.

#### Scenario: A rule is enabled or intentionally inapplicable

- **WHEN** maintainers inspect the quality responsibility map
- **THEN** the rule's owner, scope, risk, measurement, remediation, false-positive cost, and review condition are explicit
- **AND** an unexplained disabled rule or blanket suppression fails policy validation.

#### Scenario: Public Python behavior is evaluated

- **WHEN** product or repository-tool source is checked
- **THEN** imports, formatting, documentation, types, correctness, security, complexity, dependencies, and dead code are evaluated by their declared owners
- **AND** warnings and unresolved type uncertainty fail the applicable gate.

#### Scenario: Test source is evaluated

- **WHEN** test code is checked
- **THEN** formatting, imports, correctness, types, security, pytest idioms, complexity, and dead code are evaluated
- **AND** absence of a redundant test-function docstring is not a failure.

#### Scenario: A quantitative boundary changes

- **WHEN** a threshold is introduced or revised
- **THEN** its one canonical policy records the protected risk and review trigger
- **AND** no duplicate threshold in a tool or Forge projection can change the verdict.

#### Scenario: A native platform capability is claimed

- **WHEN** quality or release evidence claims macOS or Linux support
- **THEN** native execution on that operating system proves the relevant product path
- **AND** syntax checks, mocks, containers of another kernel, or a different platform's success do not substitute for that evidence.

#### Scenario: a small semantic package has a volatile ratio

- **WHEN** it has current execution evidence and the product aggregate satisfies the canonical coverage policy
- **THEN** its exact ratio remains diagnostic
- **AND** promotion is decided by aggregate product risk and package observation.

#### Scenario: A contributor reviews a large owner

- **WHEN** a production, test, or tool owner has high source-size or nesting observations
- **THEN** the repository quality command reports the measurements with the exact path
- **AND** semantic ownership, behavior, dependency direction, and review evidence determine whether refactoring is required.

#### Scenario: An undeclared package appears

- **WHEN** a package is added outside the positive package topology
- **THEN** the quality command rejects it as an undeclared semantic owner
- **AND** no parallel forbidden-name list is consulted.

#### Scenario: A contributor locates behavior

- **WHEN** a contributor follows a public command or runtime behavior
- **THEN** its implementation, tests, specification, and documentation point to one semantic owner
- **AND** no compatibility module or duplicated policy must be consulted.

#### Scenario: Coverage evidence is evaluated

- **WHEN** one small module has a volatile ratio but its semantic package and the product aggregate satisfies the canonical coverage policy
- **THEN** the file ratio remains diagnostic evidence
- **AND** no duplicate threshold in a tool-native formatting file can change the verdict.

#### Scenario: a policy changes

- **WHEN** maintainers revise a quantitative boundary
- **THEN** they update its single machine owner and recorded rationale
- **AND** repository tests reject any competing threshold source.

#### Scenario: documentation wording improves

- **WHEN** README prose changes without changing product identity, links, or release behavior
- **THEN** quality and release admission SHALL evaluate those semantic contracts
- **AND** no exact prose fragment SHALL act as a merge or publication veto.

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

- **WHEN** complexity, nesting, coverage, or performance becomes blocking
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
