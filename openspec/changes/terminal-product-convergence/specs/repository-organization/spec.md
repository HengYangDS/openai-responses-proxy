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
