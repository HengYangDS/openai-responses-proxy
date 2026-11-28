# OpenSpec 2.2.0

## Context

- `package.json` pins `@fission-ai/openspec` 2.0.0 and `prettier` 3.10.1 as development dependencies. The jobs that
  run governance (GitLab's `source-and-governance` and its accepted-branch job, GitHub's `source-and-governance` and
  `tag-metadata`) and the `bootstrap` task install them with `npm ci --ignore-scripts`. They use the npm that
  `.config/mise/config.toml` selects (12.2.3) and the repository's own npm configuration, whose release age is one day.
- Governance runs `openspec validate --all --strict` from the repository's lock.
- ETHOS 0.2.0-beta.12 runs the OpenSpec that it bundles and checks that copy against its own manifest and lock. Its
  `adapters/openspec/cli.py` takes the version from the bundled `resources/package.json`, which pins 2.0.0, and
  resolves the package under ETHOS's own root. It does not read this repository's pin.
- The repository keeps no OpenSpec agent skills. Its history holds no skill folders, `openspec/AGENTS.md` or
  `openspec/project.md`, which `openspec init` would have written.

## Goals / Non-Goals

The goal and non-goals are the proposal's.

## Decisions

### D1. Pin 2.2.0 exactly and resolve the lock again

The manifest pins 2.2.0, as it pinned 2.0.0. The selected npm resolves the lock with scripts ignored. The lock
gains nine packages: `skills` 1.8.1, through which OpenSpec 2.1 and later install agent skills, `tar` 7.5.24 with
its five dependencies, `nostics` 1.4.0, and a nested `chalk` 6.0.1. None of them declares an install script. All of
them resolve from `registry.npmjs.org` with an integrity hash. Their highest Node floor is 22.20.0 (`skills`), and
OpenSpec's own floor is 22.20.0 in 2.2.0, up from 22.12.0 in 2.0.0. Mise selects Node 26.13.1. 2.2.0 has passed the
one-day release age.

Alternative: 2.1.1. Rejected, because the owner asked for the latest stable release. Beyond 2.1.1, 2.2.0 changes only
skills and the Windows standalone installer.

### D2. No skills, and no `openspec update`

OpenSpec 2.1 installs agent skills globally, and 2.2 adds an `/openspec` router skill with reference files (release
notes of v2.1.0 and v2.2.0). These reach an agent through `openspec init` or `openspec update`, which change files
outside the specification tree. The repository keeps none of them, so this Change runs neither. Whether ETHOS
provides OpenSpec's skills to the agents of the repositories that it governs is ETHOS's decision.

### D3. What the new release changes here

The v2.1.1 release notes name the changes that reach this repository:

- `validate --strict` reports a MODIFIED requirement that grows past 500 characters. 2.2.0's
  `dist/core/validation/validator.js` reports it as a warning, and in strict mode a report is valid only with no
  errors and no warnings, so governance fails. In `relay-hygiene`, that limit was found only by ETHOS's readiness
  proof, which simulates the archive.
- A delta spec may begin with YAML front matter, which 2.0.0 rejects.
- Archive stops before it touches a main spec when the archive folder already exists. A main spec that it creates
  has a blank line after `## Purpose`. ETHOS runs its own 2.0.0 for archives, so these take effect here when ETHOS
  takes 2.1.1 or later.

## Risks / Trade-offs

- **Two OpenSpec releases validate one tree** until ETHOS moves: governance runs 2.2.0, and ETHOS's proof runs 2.0.0.
  An artifact that only one of them accepts fails governance or the proof. A delta spec with front matter is such a
  case. The repository writes none, and this Change's verification runs both releases.
