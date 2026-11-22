## Why

ETHOS runs a proof's readiness tier before the proof joins the host's queue: every gate that is neither a `test`
nor a `package` gate, and depends on none. The `python-quality` gate is declared `static`, so it runs there, and its
command, `nox -s quality`, runs the static analysis and then the whole behavior suite under coverage on Python
3.12. In the in-place proof of 2026-11-22 that gate took 231 seconds of the proof's 661, outside the queue that the
host's admission rules give every heavy run. The host's keeper found it on 11-22 and committed this repository to a
fix by 11-23; until then no proxy readiness or proof runs outside a ticket.

## What Changes

- The `python-quality` gate keeps only the static analysis: Ruff, the format check, the repository structure checks
  and the type check, on the 3.12 floor. It stays in the readiness tier, so a proof still refuses a static fault
  before it waits for the host.
- The 3.12 behavior suite under coverage, with the coverage floor, joins the `python-matrix` gate beside 3.13 and
  3.14 as a `test` gate, so it runs inside the proof's host ticket.
- The hosted quality job and `mise run check` keep running the same checks; only the proof's split between the two
  gates moves.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-diagnostics`: "Quality policy has explicit owners" gains the rule that the proof's readiness tier holds no
  behavior run.

## Impact

`.ethos/profile.toml`, `noxfile.py`, the quality orchestration tests and the CI projection if a session's name
changes. No product code and no released artifact change, so the changelog gets no entry.

- **Authority boundary:** the host's admission rules and their keeper decide what runs inside a ticket; ETHOS
  decides which gate kinds form its readiness tier. This change follows both and changes neither.
- **Proof:** the local tests show which sessions each gate runs. A full proof under a ticket shows that the readiness
  tier ends in seconds and that the coverage floor is still enforced. Hosted runs show that each Forge's quality job
  still passes.
- **Out of scope:** the coverage policy and its floor, the matrix's interpreters, and ETHOS's tier rule.
