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

- [ ] 4.1 Pass the proxy's full check (`mise run check`) on the lane head, in the host queue.
- [ ] 4.2 Pass the native macOS run (`mise run native`) on the lane head, in the host queue.
- [ ] 4.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design.

## 5. Exact-HEAD proof

- [ ] 5.1 Pass the ETHOS full proof of the lane head.

## 6. Hosted CI

- [ ] 6.1 Pass the proposal's GitHub checks.
- [ ] 6.2 Pass the proposal's GitLab pipeline, its review-role macOS jobs included, and read from their logs how long
      installing each Python version took on the guest (D3).

## 7. Publication

- [ ] 7.1 Before the first `dev` push to GitLab, read the proxy's two macOS runners back: online, assigned to the
      project, tagged as the declaration names them, and the protected one limited to protected refs.
