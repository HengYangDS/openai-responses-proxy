## Why

Hosted commit admission builds ETHOS from source commit `92253d2a8` (2026-10-21), 1,784 commits before the
0.2.0-beta.11 release that the maintainer runs locally. The repository declares no ETHOS release, so CI and local
work run different ETHOS builds, and every commit-admission job rebuilds ETHOS, its Node and Vale from source. ETHOS
0.2.0-beta.12 is released, and its maintainers asked each adopter to move its own binding.

## What Changes

- `.config/mise/config.toml` declares ETHOS 0.2.0-beta.12 by ETHOS's own pin, `"github:HengYangDS/ethos"`, with
  `locked_verify_provenance`; `.config/mise/mise.lock` records each platform's checksum and attestation provenance.
  ETHOS writes the pin through `ethos lane repair-paradigm --pin`.
- The commit-admission jobs on GitHub and GitLab install that locked release through Mise and run its
  `ethos hook bind-trust` and `ethos hook commit-range`. The source checkout, its build steps and the `PYTHONPATH`
  into it are removed.
- The local runtime follows the same pin, so local proofs and hosted admission run one ETHOS release.
- CONTRIBUTING states the one ETHOS selection.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The commit-admission contract (`ethos hook commit-range` with exact event objects and operator-managed trust)
is unchanged; only the supply of the ETHOS that runs it changes.

## Impact

- Hosted commit admission downloads the ETHOS release archive (about 383 MB for Linux x64) instead of building
  ETHOS; GitHub caches Mise installs.
- No product code, payload or release changes; no `VERSION` change.
