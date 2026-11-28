## Why

On 2026-11-28 the owner asked that every repository ETHOS governs move OpenSpec to the latest stable release, and
that ETHOS make full use of OpenSpec's capabilities. The owner approved this Change for this repository the same day.
The repository pins `@fission-ai/openspec` 2.0.0, published 2026-11-11. The latest stable release is 2.2.0, published
2026-11-24T19:58:21Z.

## What Changes

- The repository's npm manifest pins `@fission-ai/openspec` 2.2.0 exactly, in place of 2.0.0, and its lock is
  resolved again by the npm that the tool configuration selects.
- `openspec validate --all --strict`, which governance runs, now fails when a MODIFIED requirement grows past 500
  characters. 2.2.0 reports it as a warning, and strict validation fails on any warning. Before, that limit was found
  only when the archive was simulated.
- `CHANGELOG.md` records the move under Unreleased.
- The Change `relay-hygiene` is archived once it has landed on both Forges. This lane is built on its head and
  lands after it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The `ci-diagnostics` requirement "Supply-chain pins are current and reproducible" asks for current stable
releases of direct quality dependencies through one declaration per ecosystem. The `release-governance` requirement
"Dependency updates follow release age and kind" asks for the release age and a deliberate admission of tools. This
Change applies both to the npm manifest and lock. No accepted requirement names an OpenSpec release. This Change
declares `skip_specs: true`.

## Impact

`package.json`, `package-lock.json` and `CHANGELOG.md`. Archiving `relay-hygiene` applies its delta to
`openspec/specs/provider-portable-responses/spec.md` and moves the Change under `openspec/changes/archive/`.

- **Local checks and hosted CI:** governance runs the repository's OpenSpec through `npm exec --offline`, so it runs
  2.2.0 in `mise run check` and in the source-and-governance jobs of both Forges. ETHOS's own OpenSpec commands
  (status, prewrite, the strict validation and archive simulation of its proof, and `lane archive-change`) run the
  OpenSpec that ETHOS bundles, 2.0.0 in ETHOS 0.2.0-beta.12. The ETHOS proof does not run governance.
- **Publication, installation and runtime:** the release assets contain no Node tool. Installation and the running
  proxy do not change.
- **Non-goals:** Node, npm and Prettier; OpenSpec agent skills, which this repository does not keep; the OpenSpec that
  ETHOS bundles and how ETHOS uses OpenSpec's capabilities, which change in ETHOS's own releases.
