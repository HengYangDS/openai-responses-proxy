## 1. One governance graph

- [x] 1.1 Remove the `online_links` parameter and the `--online-links` option from `tools/quality/governance.py` (D1).
      Done when the module has no link-mode parameter and `governance.main(("--online-links",))` is refused by the
      parser. Refused with "Unknown option", exit 2.
- [x] 1.2 Declare `offline = true` in `.config/checks/lychee/lychee.toml`, drop `--offline` from the governance command,
      and remove `max_retries`, `timeout` and `require_https` (D2, D3). Done when lychee 0.24.2 with network access
      denied reports the same links, passes and exclusions over the tracked Markdown as the previous configuration with
      `--offline`, and exits 0. Both: 83 links, 71 passed, 12 excluded, exit 0 (D3).
- [x] 1.3 Describe the offline check in the `documentation-links` concern of
      `.config/checks/responsibilities/policy.toml`. Done when the concern names no online mode or retry.
- [x] 1.4 Update `tests/quality/orchestration/test_governance.py` and `test_selection.py` to the single graph. Done when
      both pass.

## 2. Forge pipelines

- [x] 2.1 Remove `--online-links` from `.config/ci/peers/github/source.cue` and `.config/ci/peers/gitlab/pipeline.cue`,
      then generate both pipelines with `python -m tools.ci.project --write`. Done when the projector without `--write`,
      `cue fmt --check` and `cue vet` of the CI graph pass.
- [x] 2.2 Update `tests/forge/workflows/test_verification.py` and `test_supply.py` so that each forge's governance
      command is the bare module. Done when the tests of `tests/forge/workflows` pass.

## 3. Record

- [x] 3.1 Add an entry under `## [Unreleased]` in `CHANGELOG.md`. Done when Prettier's check of it passes.

## 4. Local verification

- [x] 4.1 Pass `tests/quality` and `tests/forge/workflows` as light work, outside the `native_observer` cases, which
      need ETHOS's observer wheel, absent from the lane's environment. 660 passed, at 0.98 cores.
- [x] 4.2 Pass `openspec validate links-checked-offline --strict`.
- [ ] 4.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design.
- [ ] 4.4 Pass the full check (`mise run check`) on the lane head, in the host queue.

## 5. Exact-HEAD proof

- [ ] 5.1 Pass the ETHOS full proof of the lane head.

## 6. Hosted CI

- [ ] 6.1 Pass the proposal's GitHub checks; the governance step runs the module without an option.
- [ ] 6.2 Pass the proposal's GitLab pipeline; its source job runs the module without an option.
