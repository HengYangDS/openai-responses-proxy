## Context

`tools/quality/python.py` writes a session's native reports under the session's own name. The single-Python
`quality` session is named `quality`, so it writes `quality-ruff.sarif`, `quality-runtime.json` and
`quality-coverage.xml`; each `tests` session writes `tests-<python>.xml`, `tests-<python>-pytest.jsonl`,
`tests-<python>-coverage.xml` and `tests-<python>-runtime.json`.
`tests/quality/orchestration/test_observation.py` pins the `quality` names. `single-test-run` assumed a
`quality-3.12` suffix without reading a written file name.

## Goals / Non-Goals

**Goals:** a proof profile whose every declared report is written, with one 3.12 suite run per proof.

**Non-Goals:** judging 3.12 behaviour from reports in the proof, which ETHOS 0.2.0-beta.10 cannot do for this
session (below); any change outside the profile.

## Decisions

### Why the quality session's suite cannot be judged from its reports

Two rules of the selected runtime, each measured on 2026-11-20 by calling the runtime's own code on this
profile:

- A gate may declare one Python runtime record per session (`ethos/contracts/gates.py`, the unique
  `(format, session)` producers). Adding a pytest runtime record for `quality-3.12` beside the Ruff one is
  refused with `native_output_contract_invalid`.
- A session that declares JUnit evidence needs a pytest invocation in that runtime record
  (`ethos/adapters/gates/native/python.py`, `PythonRuntime.require_checks`). A JUnit report beside the
  Ruff-only record raises `python_native_pytest_invocation_unbound`.

So one Nox session cannot carry both SARIF and JUnit evidence under this runtime.

### How 3.12 counts in the proof

The acceptance owner's decision: `python-quality` keeps its declaration, and 3.12 counts in the proof through
that gate's exit status, which fails when the `quality` session's suite or its branch-coverage floor fails.
ETHOS judges behaviour from the 3.13 and 3.14 reports of `python-matrix`. Hosted CI on both Forges still runs
`tests-3.12`, which also byte-compiles `python_quality.ROOTS`; the proof no longer byte-compiles on 3.12 a
file that no test imports.

## Risks / Trade-offs

- **3.12 behaviour is proved by an exit status, not by reports that ETHOS reads.** A defect in the `quality`
  session's own pass or fail decision would go unseen by ETHOS; the coverage policy tool and pytest's exit
  code decide it, as before this lane.
