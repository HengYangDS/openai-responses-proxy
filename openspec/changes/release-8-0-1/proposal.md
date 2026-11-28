## Why

On 2026-11-28 the owner chose that release 8.0.1 includes the move to OpenSpec 2.2.0, which landed on both Forges'
`dev` after the 8.0.1 entries were written, and asked that 8.0.1 be published once it is ready. `CHANGELOG.md` still
lists that move under Unreleased, so a release from the current `main` would describe its contents wrongly.

## What Changes

- `CHANGELOG.md` lists the OpenSpec 2.2.0 entry under `## [8.0.1]` and leaves Unreleased empty.
- The Change `openspec-2-2-0`, landed on both Forges, is archived.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The release record changes, and no behavior does. This Change declares `skip_specs: true`.

## Impact

`CHANGELOG.md`, and the archive of `openspec-2-2-0` under `openspec/changes/archive/`. That Change has no spec delta.

- **Local proof and hosted CI:** the release metadata check reads the moved entry.
- **Publication, installation and runtime:** none in this Change. Tagging and publishing 8.0.1 follow its landing,
  as lifecycle steps.
- **Non-goals:** any other change to the 8.0.1 entries; installing or upgrading the release.
