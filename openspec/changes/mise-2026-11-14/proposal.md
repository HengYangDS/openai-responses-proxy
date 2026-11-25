## Why

On 2026-11-26 the owner asked that every Mise pin of every repository ETHOS governs move to the latest stable Mise
(05:48 and 05:49, confirmed at 06:08). The proxy's CI runs Mise 2026.11.8 on both forges, and GitHub installs it through
`jdx/mise-action` v5.2.9. The latest stable Mise is 2026.11.14, published 2026-11-24T11:00:43Z, and the latest
mise-action is v5.3.0, published 2026-11-21T17:01:38Z; both are older than the repository's 24-hour release age.

## What Changes

- GitLab's jobs run the Debian image of Mise 2026.11.14, pinned by its OCI index digest, in place of 2026.11.8. GitHub's
  mise-action installs the same release, which the CUE supply contract derives from that image's tag.
- `jdx/mise-action` moves from v5.2.9 to v5.3.0, pinned by its commit.
- Both forge pipelines are generated again from the CUE supply contract.
- The Mise floor in `.config/mise/config.toml` stays 2026.10.14. It is not a pin: it is the oldest Mise that may run the
  repository, and ETHOS's CI supply runs Mise 2026.10.14 here for commit admission.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The `ci-diagnostics` requirement "Supply-chain pins are current and reproducible" and its scenario "The supply
chain advances" already state this behavior: a stable CI base image or hosted Action release that supersedes a pin is
taken in its existing owner, without a parallel version owner. No accepted requirement names a Mise release, image or
action revision. This Change declares `skip_specs: true`.

## Impact

`.config/ci/graph/toolchains.cue` and its projections `.gitlab-ci.yml` and `.github/workflows/verify.yml`, and
`CHANGELOG.md`.

- **Local proof and hosted CI:** only the forge pipelines read the pin. A local check or proof runs the host's own Mise,
  at or above the floor, so only the hosted jobs on both forges exercise the new release.
- **Publication, installation and runtime:** the release assets contain no Mise; installation and the running proxy do
  not change.
- **Authority boundary:** `#Toolchains` in the CUE graph owns the CI supply, and the two pipelines are its projections.
  `.config/mise/mise.lock` owns the tools that Mise installs; this Change does not edit it.
- **Non-goals:** the Mise floor; the tools that Mise installs and their versions; the runner-installed Mise of the GitLab
  macOS job that Change `gitlab-macos-python-job` adds; the released program.
