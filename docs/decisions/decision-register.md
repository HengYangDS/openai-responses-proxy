---
subject: openai-responses-proxy:decision-record-conventions
role: reference
state: canonical
relations:
    part_of: README.md
---

# Decision Records

Decision Records capture durable choices that constrain product design or
operations. OpenSpec owns the lifecycle of a change; a Decision Record owns the
rationale that must remain after the change is complete.

## File grammar

Project-owned records use:

```text
dr-<four-digit-sequence>-<concise-kebab-case-description>.md
```

The sequence is stable and never reused. A record is amended or superseded; it
is not silently rewritten into a different decision. Tool-mandated names such
as `pyproject.toml`, `spec.md`, and `__init__.py` keep their native grammar.

## Required sections

Every record contains Status, Date, Context, Decision, Consequences, and Revisit
Trigger. Add alternatives or evidence only when they clarify the ruling.

## Coverage

Create a record when a choice is durable and at least one of these applies:

- it establishes a product or authority boundary;
- it selects or rejects a foundational dependency or architecture;
- it defines compatibility, security, release, or data-retention policy;
- it is expensive or unsafe to reverse;
- a future maintainer could reasonably undo it without the rationale.

Do not create records for routine implementation details, temporary incidents,
or facts already owned by a specification. The current register is:

| Record                                                          | Decision                                                                      |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [DR-0001](dr-0001-control-plane-data-plane-boundary.md)         | Keep client control planes separate from the proxy data plane.                |
| [DR-0002](dr-0002-provider-portable-stateless-replay.md)        | Keep replay stateless and portable across providers.                          |
| [DR-0003](dr-0003-provider-scoped-recovery-and-backpressure.md) | Isolate recovery and backpressure by provider route.                          |
| [DR-0004](dr-0004-local-first-independent-forge-release.md)     | Keep local closure and independent Forge publication.                         |
| [DR-0005](dr-0005-single-native-payload-lifecycle.md)           | Maintain one current native payload and lifecycle model.                      |
| [DR-0006](dr-0006-semantic-fit-before-framework-adoption.md)    | Adopt mature frameworks only where they replace an owned complexity boundary. |
