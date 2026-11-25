## 1. Specification

- [x] 1.1 Modify `ci-diagnostics` so that GitHub and GitLab both run the macOS Python job from one declaration and GitLab
      takes its macOS interpreters from the locked Mise, keeping every scenario name. Done when
      `openspec validate gitlab-macos-python-job --strict` passes and the archive rehearsal passes in both orders with
      `terminal-product-convergence`.

## 2. CI declaration

- [x] 2.1 Add `macos.gitlab` with the review and protected tag pairs to `.config/ci/graph/platforms.cue`, declare the
      compatibility job's timeout once in the verification contract, and render GitLab's macOS Python job for both
      roles from them, with no inherited default or variable, its own target, uv and Mise directories inside the project
      and a cache key per target and interpreter, a record of the runner's Mise, the Linux job's steps run through the
      locked Mise, and no allowed failure (D1 to D4). Done when `python -m tools.ci.project` reports no drift, the diff
      of `.gitlab-ci.yml` adds only the two macOS jobs, and no GitLab step is inline shell.
- [x] 2.2 Pin by test that GitLab's macOS job exists for both roles on the declared tag pairs, inherits nothing, is
      required and mirrors GitHub's macOS Python job, that its steps are the Linux job's run through the runner's Mise
      and its tools stay inside the project cache, that a changed declaration moves its tags alone, and that every
      other GitLab job stays on its Linux runners. Done when the `tests/forge`, `tests/ci` and
      `tests/quality/test_verification.py` tests pass, the repository-toolchain ones included.

## 3. Records

- [x] 3.1 Add an entry under `## [Unreleased]` in `CHANGELOG.md` and correct each CI document or comment that says
      GitLab runs only Linux. Done when `python -m tools.release.metadata` and the prose and Markdown checks pass.
      Only the platform declaration's comment said so; no document did.

## 4. Local gates

- [x] 4.1 Pass the proxy's full check (`mise run check`) on the lane head, in the host queue. `e8ffe570`, 2026-11-26
      06:15: 2,956 passed, 5 skipped.
- [x] 4.2 Pass the native macOS run (`mise run native`) on the lane head, in the host queue. `e8ffe570`, 06:24: 74
      passed, 1 skipped.
- [x] 4.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design. Seven findings answered in `14b5c4ba`; the readiness gap of its Mise check answered in
      `8308254d` and `e8ffe570`.

## 5. Exact-HEAD proof

- [x] 5.1 Pass the ETHOS full proof of the lane head. `e8ffe570`, 06:44: pass, proven.

## 6. Hosted CI

- [x] 6.1 Pass the proposal's GitHub checks. Pull request 221 at `e8ffe570`, 07:37: 15 passed, 5 skipped.
- [x] 6.2 Pass the proposal's GitLab pipeline, its review-role macOS jobs included, and read from their logs how long
      installing each Python version took on the guest (D3). Pipeline 18231 at `e8ffe570`, 07:36: the runner's Mise
      2026.11.14; `uv python install` 3.99 s (3.12), 4.05 s (3.13), 4.58 s (3.14); Mise's locked Python 3.14 8.2 s,
      6.5 s and 100.4 s; jobs 256 s, 246 s and 399 s, one after another, with an empty first cache.

## 7. Publication

- [ ] 7.1 Before the first `dev` push to GitLab, read the proxy's two macOS runners back: online, assigned to the
      project, tagged as the declaration names them, and the protected one limited to protected refs.
