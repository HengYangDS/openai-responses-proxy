## ADDED Requirements

### Requirement: Repository structure follows domain semantics

Each retained directory and file SHALL represent one precise product or
repository-tool concept, have one primary reason to change, and use a name that
communicates that concept without concatenation, arbitrary suffixing, or a vague
catch-all role. Source, tests, tools, configuration, specifications,
documentation, and generated projections SHALL use the same vocabulary and
point to the same owner.

#### Scenario: A contributor traces one behavior

- **WHEN** the contributor starts from a public command, specification, test,
  configuration field, or document
- **THEN** the path leads to one semantic implementation owner
- **AND** no parallel module, duplicate registry, compatibility facade, or
  stale term must be consulted.

#### Scenario: A repository carrier has no current purpose

- **WHEN** it has no unique invariant, current consumer, or required recovery role
- **THEN** it is deleted in the same convergence task that removes its references
- **AND** it is not moved into a miscellaneous, legacy, archive, or records area.

### Requirement: Each Work Lane reconstructs its locked environment

Every Work Lane SHALL independently reconstruct mutable `.venv`, `.nox`,
`node_modules`, build, coverage, and temporary state from committed locks
through one cross-platform developer entrypoint. Work Lanes MAY share only
content-addressed tool and package caches; they SHALL NOT share mutable virtual
environments or depend on ambient system tools.

#### Scenario: A clean Work Lane is bootstrapped

- **WHEN** a contributor starts from the exact repository revision with empty
  project-local environments
- **THEN** one documented locked command reconstructs every required tool and dependency
- **AND** a second bootstrap produces no source or lock diff.

## MODIFIED Requirements

### Requirement: Portable repository quality surface

Forge publication SHALL project existing local Git objects exactly. The adopted
ETHOS publication command SHALL be the sole branch projector; Proxy source SHALL
NOT define a second branch publication command. Provider-specific history
reconstruction, identity rewriting, continuity maps, and replay receipts SHALL
NOT remain as parallel publication authorities.

#### Scenario: A maintainer traces branch publication

- **WHEN** the selected Forge branch is advanced
- **THEN** ETHOS publication verifies the local object and exact remote CAS
  coordinate
- **AND** pushes that object without creating another commit
- **AND** no repository-owned projector, history-mapping module, or
  compatibility flag is consulted.
