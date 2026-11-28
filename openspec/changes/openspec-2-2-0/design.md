# OpenSpec 2.2.0

## Context

- `package.json` pins `@fission-ai/openspec` 2.0.0 and `prettier` 3.10.1 as development dependencies. Every job and
  the `bootstrap` task install them with `npm ci --ignore-scripts`, through the npm that `.config/mise/config.toml`
  selects (12.2.3) and the repository's own npm configuration. That configuration's release age is one day.
- Governance runs `openspec validate --all --strict` from the repository's lock.
- ETHOS 0.2.0-beta.12 runs the OpenSpec that it bundles, 2.0.0, and checks that copy against ETHOS's own manifest
  and lock. It does not read this repository's pin.
- The repository keeps no OpenSpec agent skills, and it has never run `openspec init` or `openspec update`.

## Goals / Non-Goals

**Goals:** the repository's own OpenSpec is the latest stable release, from the one manifest and lock that own it.

**Non-Goals:** the OpenSpec that ETHOS bundles; agent skills; Node, npm and Prettier.

## Decisions

### D1. Pin 2.2.0 exactly and resolve the lock again

The manifest pins 2.2.0, as it pinned 2.0.0. The selected npm resolves the lock with scripts ignored. The lock
gains nine packages: `skills` 1.8.1, through which OpenSpec 2.1 and later install agent skills, `tar` 7.5.24 with
its five dependencies, `nostics` 1.4.0, and a nested `chalk` 6.0.1. None of them runs an install script. All of
them resolve from `registry.npmjs.org` with an integrity hash, and they need at most Node 22.20.0, below the
26.13.1 that Mise selects. npm's advisory database listed no advisory for the lock before or after the change on
2026-11-28. 2.2.0 has passed the one-day release age.

Alternative: 2.1.1. Rejected, because the owner asked for the latest stable release, and 2.2.0 changes only skills
beyond it.

### D2. No skills, and no `openspec update`

OpenSpec 2.1 installs agent skills globally, and 2.2 adds an `/openspec` router skill with reference files. These
reach an agent through `openspec init` or `openspec update`, which change files outside the specification tree. The
repository keeps none of them, so this Change runs neither. Whether ETHOS provides OpenSpec's skills to the agents
of the repositories that it governs is ETHOS's decision.

### D3. What the new release changes here

- 2.1.1's `validate --strict` warns when a MODIFIED requirement grows past 500 characters. In `relay-hygiene`, that
  limit was found only by ETHOS's readiness proof, which simulates the archive.
- 2.1.1's archive stops before it touches a main spec when the archive folder already exists. A main spec that it
  creates has a blank line after `## Purpose`. ETHOS runs its own 2.0.0 for archives, so these take effect here
  when ETHOS takes 2.1.1 or later.

## Risks / Trade-offs

- **Two OpenSpec releases validate one tree** until ETHOS moves: governance runs 2.2.0, and ETHOS runs 2.0.0. Both
  must accept the specifications, and an artifact that only one of them accepts fails the proof. This Change's
  verification runs both.
