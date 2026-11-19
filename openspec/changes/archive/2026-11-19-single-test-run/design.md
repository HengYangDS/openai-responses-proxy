## Context

`tools/quality/python.py` owns the suite. Its `behavior` function runs pytest once per Nox session and,
when ETHOS's output directory is set, names the reports by the session: the `quality` session on 3.12
writes `quality-3.12.xml`, `quality-3.12-pytest.jsonl`, `quality-3.12-coverage.xml` and
`quality-3.12-runtime.json`, and each `tests` session writes the same four for its Python. Until now the
proof declared only the `tests` sessions' reports, so it ran the suite on 3.12 twice.

ETHOS 0.2.0-beta.10 judges a gate's declared native outputs and runs no test itself
(`ethos/adapters/gates/native/evidence.py`, read on 2026-11-20). Every declared session must appear in the
gate's Nox report; every JUnit report must pass and pair with its pytest events; a coverage report must come
from a tested session. The behaviour obligation joins the files claimed by every gate whose dimensions name
`behavior`, and requires that union to equal the proof's behaviour subjects. A Python runtime record may
name a session only once per gate (`python_native_toolchain_unbound`).

## Goals / Non-Goals

**Goals:** one 3.12 suite run per proof; each supported Python still judged from its own reports; the
noxfile and CI unchanged.

**Non-Goals:** hosted CI, which keeps a 3.12 test node; the coverage policy.

## Decisions

### `python-quality` contributes the 3.12 behaviour evidence

The gate adds `behavior` to its dimensions, the behaviour verifier to its verification providers, and
declares `quality-3.12.xml`, `quality-3.12-pytest.jsonl` and `quality-3.12-coverage.xml`. It does not
declare `quality-3.12-runtime.json`: the gate already declares the session's runtime record for Ruff
(`quality-ruff-runtime.json`, the same interpreter and environment), and a second record for the session
is refused. `python-matrix` stays the mapped behaviour gate and runs `nox -s tests-3.13 tests-3.14`.

Alternatives considered:

- **Drop 3.12 from the matrix and judge it by `quality`'s exit status only.** Rejected: the compatibility
  floor would no longer be judged from its own reports.
- **Move the coverage policy into `tests-3.12` and make `quality` static only.** Rejected: it changes the
  noxfile and both Forge graphs for the gain that the profile already gives.

### What the proof no longer does on 3.12

The `tests` session also byte-compiles `python_quality.ROOTS` (`python -m compileall`) before the suite;
`quality` does not. The proof therefore no longer byte-compiles on 3.12 a file that no test imports. Ty
checks the code at the 3.12 floor in `quality`, and both Forge graphs still run `tests-3.12` with its
byte-compilation.

## Risks / Trade-offs

- **A code path that runs only on 3.12 and no longer on 3.13 or 3.14** is still covered: `quality` measures
  it under the same coverage policy, and ETHOS reads that report.
