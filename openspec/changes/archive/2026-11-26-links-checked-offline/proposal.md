## Why

Both forges run the repository's governance check with `--online-links`, so lychee fetches every external URL in the
tracked Markdown, while the local check (`mise run check`) runs the same governance check offline. GitHub's HTML pages
answer the GitLab Linux runners with 503 or 504 from time to time: GitLab jobs failed on lychee alone on seven days
between 2026-10-17 and 2026-11-26, twice on the last, while the same URLs answered 200 from the host. Each such failure
reports no defect of this repository, cannot be reproduced by the local check, and costs a rerun.

## What Changes

- **BREAKING (repository tooling):** the governance check loses its `--online-links` option and its online mode. It has
  one graph, and the link check in it makes no network request.
- The lychee configuration declares the offline mode itself and keeps only the settings that have an effect offline:
  anchor fragments and the root of absolute local links. `max_retries`, `timeout` and `require_https` are removed.
- Both forge pipelines are generated again from the CUE sources without the option, so GitHub, GitLab and the local
  check run the same link check.
- The responsibility map's `documentation-links` concern describes the offline check.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-diagnostics`: links are checked without the network, by the same check on both forges and in the local check.

## Impact

`tools/quality/governance.py`, `.config/checks/lychee/lychee.toml`, `.config/checks/responsibilities/policy.toml`, the
CUE sources `.config/ci/peers/github/source.cue` and `.config/ci/peers/gitlab/pipeline.cue` with their projections
`.github/workflows/verify.yml` and `.gitlab-ci.yml`, the tests that pinned the option, and `CHANGELOG.md`.

- **Local check and proof:** unchanged in effect. `mise run check` already checked links offline; the ETHOS full proof
  runs no link check, before or after this Change.
- **Hosted CI:** both forges stop fetching external URLs. A broken local link or anchor still fails the job.
- **Publication, installation and runtime:** unchanged. No release asset contains the governance check.
- **Authority boundary:** `.config/checks/lychee/lychee.toml` owns lychee's policy, its mode included;
  `tools.quality.governance` owns the one governance graph; the CUE sources own the forge jobs, and the two pipelines
  are their projections.
- **Non-goals:** checking external URLs anywhere, whether in CI, the local check, the proof or a scheduled job;
  aigw-cli's link check, which is already offline; the product and its release assets.
