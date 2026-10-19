---
subject: openai-responses-proxy:decisions
role: index
state: canonical
relations:
  part_of: ../README.md
---

# Decisions

Decision Records explain durable choices and rejected alternatives that remain
useful across Changes. OpenSpec owns capability requirements and active progress;
ETHOS's Docs Registry owns the shared identity and rationale contract. This page
is the only decision navigation source.

| Record                                                          | Decision                                                                 |
| --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| [DR-0001](dr-0001-control-plane-data-plane-boundary.md)         | Keep client control planes separate from the proxy data plane.           |
| [DR-0002](dr-0002-provider-portable-stateless-replay.md)        | Keep replay stateless and portable across admitted providers.            |
| [DR-0003](dr-0003-provider-scoped-recovery-and-backpressure.md) | Isolate recovery and backpressure by provider route.                     |
| [DR-0004](dr-0004-local-first-independent-forge-release.md)     | Publish one signed bundle through independent Forges.                    |
| [DR-0005](dr-0005-single-native-payload-lifecycle.md)           | Maintain one native payload and lifecycle model.                         |
| [DR-0006](dr-0006-semantic-fit-before-framework-adoption.md)    | Adopt dependencies only where they replace an owned complexity boundary. |

Each stable positive decision number identifies one ruling and is never reused.
Preserve known decision and amendment dates; accepted rationale is not proof of
implementation, installation or publication. Keep executable procedures and
execution bodies with their existing native owners, not in the rationale.

Create a record only for an irreducible cross-Change choice, such as an authority
boundary, foundational dependency, compatibility policy or expensive reversal.
Routine implementation, incidents and facts already owned by a specification
need no ceremonial record. Amend the same ruling, or retire it when its current
contract preserves the rationale and revisit condition without a separate record.
