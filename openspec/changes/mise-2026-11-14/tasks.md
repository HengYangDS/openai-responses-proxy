## 1. Supply contract

- [x] 1.1 Move `gitlabMiseImage` in `.config/ci/graph/toolchains.cue` to `ghcr.io/jdx/mise:2026.11.14-debian` pinned by
      its index digest (D1). Done when the digest equals the registry's index digest for that tag.
- [x] 1.2 Move `githubActions.mise` to `jdx/mise-action` v5.3.0 by its commit, with its version comment (D2). Done
      when the commit equals the one that the `v5.3.0` tag names on GitHub.
- [x] 1.3 Generate both forge pipelines again with `python -m tools.ci.project --write`. Done when the projector
      without `--write` and `cue fmt --check` of the CI graph both pass.

## 2. Lock and record

- [x] 2.1 Run `mise install --locked` in the lane with Mise 2026.11.14. Done when it passes and
      `.config/mise/mise.lock` is byte-identical to the base. It passed into an empty private data directory (11 tools
      installed) and with the shared one; `mise lock --check` passes too.
- [x] 2.2 Add an entry under `## [Unreleased]` in `CHANGELOG.md`. Done when Prettier's check of it passes.

## 3. Local verification

- [x] 3.1 Pass the supply and admission tests of `tests/forge/workflows`, with the `repository_toolchain` cases, as
      light work. 51 passed, 22 of them `repository_toolchain`.
- [x] 3.2 Pass `openspec validate mise-2026-11-14 --strict`.
- [ ] 3.3 Pass the proxy's full check (`mise run check`) on the lane head, in the host queue.
- [ ] 3.4 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design.

## 4. Exact-HEAD proof

- [ ] 4.1 Pass the ETHOS full proof of the lane head.

## 5. Hosted CI

- [ ] 5.1 Pass the proposal's GitHub checks; each mise-action step installs Mise 2026.11.14.
- [ ] 5.2 Pass the proposal's GitLab pipeline on the Linux runners from the 2026.11.14 image.
