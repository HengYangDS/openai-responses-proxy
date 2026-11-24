## Why

The repository's Node tools (OpenSpec and Prettier) are installed and audited by npm, and no tracked file selects
which npm. Node 26.13.1 bundles npm 11.20.0, whose bundled packages carry 19 OSV advisories (6 high, 12 moderate, 1
low; relayed by the ETHOS onboarding session on 2026-11-24 at 12:09); npm 12.2.3 bundles two moderate ones. Hosted
jobs on both Forges run the bundled npm. On the owner's host the same commands run npm 12.2.3 from the operator's
global Mise configuration, so local and hosted verification already run different package managers.

## What Changes

- Mise selects the package manager npm, pinned exactly at its latest stable release (12.2.3, published 2026-11-18)
  and locked by integrity, so every Forge job and every checkout run the same npm.
- The GitLab source job enables the pinned npm with the other quality tools.
- Repository tools (OpenSpec, Prettier and their graph) stay owned by `package.json` and `package-lock.json`.

No product behavior changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-governance`: the repository's tool configuration selects the package manager that installs its locked
  Node tools.

## Impact

`.config/mise/config.toml`, `.config/mise/mise.lock` and its npm lock, the CUE supply contract and the projected GitLab
pipeline, the supply contract test, and `CONTRIBUTING.md`. Out of scope: the operator's global Mise configuration,
which this Change does not edit, and why the project's `miserc.toml` does not keep it out (recorded in the design).
