## MODIFIED Requirements

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
