## Why

Every exact-HEAD proof runs the full test suite on Python 3.12 twice, one run after the other. The
`python-quality` gate runs the Nox session `quality`, which runs the suite on 3.12 with the branch-coverage
policy; the `python-matrix` gate then runs the session `tests` on 3.12, 3.13 and 3.14. In the proofs of
`6d1b384f` and `d4029154` the first 3.12 run took 217 to 219 s and the second 218 to 230 s, over the same
2968 and 2893 tests; the whole proof took 941 and 907 s. On 2026-11-18 the repository owner asked for the
utmost performance in every ETHOS-governed repository, and on 2026-11-20 approved running the 3.12 suite
once per proof.

## What Changes

- The proof's `python-matrix` gate runs only the `tests` sessions of 3.13 and 3.14.
- The `python-quality` gate declares the test and coverage reports that its `quality` session already writes
  on 3.12, and ETHOS judges 3.12 behaviour from them beside the 3.13 and 3.14 reports of `python-matrix`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The accepted requirement that each supported Python version has an independently observable test node
governs the Forge graphs, which this Change leaves as they are; the proof still judges each supported Python
from its own reports. This Change declares `skip_specs: true`.

## Impact

`.ethos/profile.toml` only.

- **Authority boundary:** the Nox sessions keep the suite, the coverage policy and its threshold; ETHOS keeps
  the proof verdict. The hosted graphs on GitHub and GitLab keep their per-Python jobs.
- **Non-goals:** the noxfile, the CI graph, the coverage policy and the released program.
