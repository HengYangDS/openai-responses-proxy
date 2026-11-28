## Why

On 2026-11-28 the owner asked that every repository ETHOS governs move OpenSpec to the latest stable release, and
that ETHOS take full use of OpenSpec's capabilities. The owner approved this Change for this repository the same day.
The repository pins `@fission-ai/openspec` 2.0.0, published 2026-11-11. The latest stable release is 2.2.0, published
2026-11-24T19:58:21Z.

## What Changes

- The repository's npm manifest pins `@fission-ai/openspec` 2.2.0 exactly, in place of 2.0.0, and its lock is
  resolved again by the npm that the tool configuration selects.
- `openspec validate --all --strict`, which governance runs, now warns when a MODIFIED requirement grows past 500
  characters. Before, that limit was found only when the archive was simulated.
- `CHANGELOG.md` records the move under Unreleased.
- The Change `relay-hygiene`, landed on both Forges, is archived.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The requirement "The repository selects the package manager of its Node tools" already keeps the Node tools
owned by the npm manifest and lock, and no accepted requirement names an OpenSpec release. This Change declares
`skip_specs: true`.

## Impact

`package.json`, `package-lock.json` and `CHANGELOG.md`.

- **Local proof and hosted CI:** governance on both Forges and in the proof runs the repository's OpenSpec through
  `npm exec --offline`, so it runs 2.2.0. ETHOS's own OpenSpec commands (status, prewrite, the archive simulation of
  its readiness proof and `lane archive-change`) run the OpenSpec that ETHOS bundles, 2.0.0 in ETHOS 0.2.0-beta.12.
- **Publication, installation and runtime:** the release assets contain no Node tool. Installation and the running
  proxy do not change.
- **Non-goals:** Node, npm and Prettier; OpenSpec agent skills, which this repository does not keep; the OpenSpec that
  ETHOS bundles and how ETHOS uses OpenSpec's capabilities, which change in ETHOS's own releases.
