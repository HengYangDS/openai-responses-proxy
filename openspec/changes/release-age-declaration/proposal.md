## Why

Since 2026-11-19 every repository that ETHOS governs holds a new release for 24 hours before it takes it, and
writes that age itself; a security fix skips it. This repository's age is still zero, and it is written in one
place only, the dependency proposal policy, which governs what Renovate proposes. A lock refresh by hand under uv
or npm therefore follows no age.

The owner's design rule of 2026-11-20 asks that a fact has one source and that what repeats it is derived or
filled from that source, never kept beside it.

## What Changes

- The repository declares its release age once, in whole hours: Mise's own `minimum_release_age` in the tracked
  Mise configuration, set to 24 hours.
- `mise run fix` fills the three other places that hold an age, each in its resolver's own setting: Renovate's
  root age, uv's `exclude-newer` and npm's `min-release-age`. `mise run check` fails while one of them differs
  from what the declaration gives.
- uv checks its lock against its age, so this change carries the refreshed `uv.lock`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-governance`: one added requirement, "One declared release age fills every resolver".

## Impact

`.config/mise/config.toml`, `.config/mise/uv.toml`, `.config/npm/npmrc`, the Renovate policy, `uv.lock`, one
quality module with its tests, the `fix` session, the governance graph, the governance document and the hard-coding
policy. No product code and no released artifact changes, so the changelog gets no entry.

- **Authority boundary:** the age and its unit are the owner's (24 hours, whole hours, a security fix skips it).
  The AIGW and Proxy session accepts the change. ETHOS ships no release-age reader yet; when it does, this
  repository's fill gives way to it and the declaration stays where it is.
- **Proof:** local tests and `mise run check` prove the fill and the check. The hosted jobs prove that each
  platform installs under the age. Nothing here proves what Renovate proposes on a Forge; no maintenance run is
  part of this change.
- **Non-goals:** the hosted Mise release, the versions of any dependency, and the sentence of the active Change
  `terminal-product-convergence` that names the proposal policy as the place where the age is declared.
