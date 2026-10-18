## ADDED Requirements

### Requirement: Logical and physical topology are one positive model

The repository SHALL positively declare the semantic owner, role, allowed
dependency direction, public entrypoint, generated status, and retirement
condition for every tracked source, test, tool, configuration, specification,
documentation, workflow, schema, and root carrier. Physical packages and paths
SHALL mirror that model. Undeclared and multiply owned entities SHALL fail
admission without a historical forbidden-name list.

#### Scenario: A repository entity is added or moved

- **WHEN** repository quality evaluates the candidate tree
- **THEN** the entity resolves to exactly one declared semantic owner
- **AND** its imports, consumers, location, and name conform to the owner's
  dependency direction and vocabulary.

#### Scenario: A flat suffix family or catch-all owner exists

- **WHEN** files are distinguished by Provider, platform, action, or vague role
  suffixes instead of a semantic package boundary
- **THEN** convergence absorbs, precisely renames, splits, or deletes the owner
- **AND** no wrapper, alias, re-export, or forwarding compatibility path remains.

### Requirement: Quality rules are complete, rational, and singly owned

Every applicable engineering concern SHALL have one mature tool or
product-semantic owner. Each blocking policy SHALL state scope, protected risk,
measurement, false-positive cost, remediation, and review condition. Warnings,
blanket ignores, permanent baselines, duplicate thresholds, and unexplained
disabled rules SHALL fail admission.

#### Scenario: The responsibility map is checked for completeness

- **WHEN** maintainers assess the applicable engineering concerns
- **THEN** the map covers formatting, import, correctness, modernization, naming,
  documentation, typing, exception, logging, security, dependency, architecture,
  dead-code, test, prose, configuration, link, workflow, secret, license,
  vulnerability, coverage, complexity, performance, commit, and release
- **AND** each concern has exactly one mature tool or product-semantic owner.

#### Scenario: A quality rule is reviewed

- **WHEN** maintainers inspect the responsibility map and native tool configuration
- **THEN** the rule has one authority and a proportionate evidence model
- **AND** custom code exists only where no mature tool can express the required
  repository or product semantic.

#### Scenario: A numeric threshold is proposed

- **WHEN** source size, complexity, nesting, parameters, coverage, or performance
  becomes blocking
- **THEN** the threshold derives from an explicit risk and observed distribution
- **AND** a stricter number is not accepted merely because it is smaller.

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
