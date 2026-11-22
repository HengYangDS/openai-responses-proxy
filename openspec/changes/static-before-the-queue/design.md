## Context

A full proof under ETHOS 0.2.0-beta.11 runs in two tiers. `readiness_tier` in ETHOS's gate runner takes every gate
that is neither a `test` nor a `package` gate and depends on none, and runs it "without waiting" (ETHOS's command
plane reference); the test and package gates then run inside the host ticket. This repository declares two code
gates in `.ethos/profile.toml`:

| Gate             | Kind     | Command                       | Duration on 2026-11-22 (proof of `1155935d`) |
| ---------------- | -------- | ----------------------------- | -------------------------------------------- |
| `python-quality` | `static` | `nox -s quality` (3.12)       | 231 s                                        |
| `python-matrix`  | `test`   | `nox -s tests-3.13 tests-3.14` | 394 s                                        |

`nox -s quality` runs `python_quality.static` and then `python_quality.behavior(require_coverage=True)`, the whole
behavior suite under coverage with the floor check, and the native observer cases when ETHOS selects native output
(`noxfile.py`, the `quality` session). So the readiness tier runs a behavior suite. The host's admission rules count
a test run over more than one package at its default width as heavy, which takes a ticket.

## Goals / Non-Goals

**Goals:** the readiness tier holds static checks only; the 3.12 behavior run, its coverage floor and its native
reports run inside the ticket; the hosted jobs and `mise run check` check the same things as before.

**Non-Goals:** a change of ETHOS's tier rule; the coverage policy or its floor; the matrix's interpreters; the speed
of the suite.

## Decisions

### Two sessions where one ran both

- **Rests on:** the session's body above, and the profile's two code axes: ETHOS requires `code_correctness_map` to
  name exactly `behavior` and `static-analysis`, each a gate of the proof (ETHOS's profile validation).
- **Considered:** declaring `python-quality` a `test` gate (the static faults would then be found only after the
  wait for the host); keeping one session and giving ETHOS a flag (a second spelling of one choice); moving the
  coverage run to 3.13 (the floor is measured on the compatibility floor by the coverage concern's owner).
- **Chosen because:** each gate then does one kind of work, the static axis stays where it fails fast, and the
  behavior axis gains its third interpreter in the same gate as the other two.

The `quality` session keeps its name and runs the static analysis only. A new session runs the 3.12 behavior suite
under coverage with the floor and the observer cases; `python-matrix` runs it beside `tests-3.13` and `tests-3.14`.
`mise run check` (`nox -s full`) and the hosted quality job run both, so no check is lost outside the proof.

## Risks / Trade-offs

- **The matrix gate grows by the 3.12 run** (about 230 s on 11-22), inside the ticket. That is the purpose; a proof
  holds the slot longer by the same time it used to spend before the queue.
- **A hosted quality job that runs only `nox -s quality`** would lose the coverage floor. The CI projection is
  generated from `.config/ci/graph/proof.cue`, and the projection tests read the sessions each job runs, so the
  graph names both sessions for the quality job.
- **Native output selection:** the observer cases and the coverage XML ran in the `quality` session; they move with
  the behavior run, so the profile's native outputs of `python-quality` keep only the static reports.
