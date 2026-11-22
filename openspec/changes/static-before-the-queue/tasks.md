## 1. The Split

- [x] 1.1 Split the `quality` session into the static analysis and a new 3.12 behavior session under coverage with
      the floor and the observer cases; `nox -s full` and the hosted quality job run both. Done when the selection
      test reads every marker selected by a gate and the projection tests read both sessions in the quality job.
- [x] 1.2 Give `python-quality` the static session alone with its static reports, and `python-matrix` the new
      session beside `tests-3.13` and `tests-3.14` with its coverage and runtime reports. Done when a test reads
      the profile and finds no pytest run in a gate that is neither `test` nor `package`.

## 2. Verification

- [ ] 2.1 Pass `mise run check` on the lane head.
- [ ] 2.2 Pass a full proof of the lane head inside a host ticket, with the readiness tier ending in seconds.
- [ ] 2.3 Pass the proposal's hosted checks on both forges.
