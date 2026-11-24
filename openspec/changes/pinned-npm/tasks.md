## 1. Pin npm

- [x] 1.1 Select npm 12.2.3 in `.config/mise/config.toml` through the `npm:npm` alias and lock it with Mise's own
      `mise lock`. Done when `mise exec --locked -- npm --version` prints 12.2.3 with the operator's global
      configuration excluded by `MISE_GLOBAL_CONFIG_FILE`, and the lock holds its integrity entry.
- [x] 1.2 Name npm in the GitLab source job's enabled tools through the CUE supply contract, and project the workflows
      again. Done when the projection check passes and `.gitlab-ci.yml` enables npm.
- [x] 1.3 Make the supply test require the selected npm on both Forges' install paths and keep refusing every other
      `npm:` tool. Done when it fails on the parent commit's configuration and passes on this one.
- [x] 1.4 Select the supply test's jobs by what they run on both Forges, and refuse a lock folder that `mise.lock` does
      not name. Done when each of the fresh review's five breaking cases fails the test and the lane passes it.
- [x] 1.5 Make governance refuse, before any Node tool runs, an npm on PATH other than the selected one. Done when
      `tools.quality.npm_supply` passes on the host and its tests cover a match, a mismatch, an unselected npm and an
      npm that does not run.
- [x] 1.6 Keep npm's notices out of successful runs through `.config/npm/npmrc`. Done when `npm exec` prints nothing on
      success.

## 2. Verification

- [x] 2.1 Pass `mise run check` on the lane head.
- [x] 2.2 A fresh review of the lane's whole change by a reviewer who has not seen it. Done when its findings are
      answered in code or in this design.
- [x] 2.3 Pass the proposal's hosted checks on both Forges (GitHub run 45438883744; GitLab pipeline 17872).
