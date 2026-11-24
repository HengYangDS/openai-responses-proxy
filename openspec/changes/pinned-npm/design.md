## Context

The proposal gives the gap. Three facts bear on the design, read on 2026-11-24 in this lane:

- `mise exec --locked -- npm --version` printed 12.2.3 on the owner's host. `mise ls --current` named its source as
  `~/.config/mise/config.toml`, the operator's global configuration; the project selected no npm. Node 26.13.1's own
  `lib/node_modules/npm` is 11.20.0.
- `mise config ls` in this checkout lists the operator's global configuration, although `.config/miserc.toml` lists it
  under `ignored_config_paths`. The ceiling does work: a parent configuration above it is not found (the fresh review
  measured this on a temporary tree, with the file and without it). AIGW's CONTRIBUTING records the same limit: a
  project `miserc.toml` "cannot retroactively exclude global or system configuration read at startup".
- AIGW pins the same npm the same way (`[tool_alias] npm = "npm:npm"`, `npm = "12.2.3"`, an integrity lock under
  `.config/mise/locks/npm/`); its hosted jobs pass with it on both Forges, and its `min_version` of 2026.10.25 reads
  that lock entry on the CI guests that this repository's native jobs share.

## Goals / Non-Goals

**Goals:** one npm, selected and locked by the repository, for every job and checkout that installs or runs the
repository's Node tools, and a check on each run that the npm on PATH is that one.

**Non-Goals:** moving OpenSpec or Prettier into Mise (the 2026-08-29 Change `deterministic-node-toolchain` gave them
one owner, the npm lock); ETHOS's own tool supply in the commit-admission jobs, which ETHOS installs and owns; editing
the operator's global Mise configuration.

## Decisions

### npm is a Mise tool, locked by integrity, and checked where it runs

- **Rests on:** the facts above; npm's own package ships its dependencies bundled, so one integrity entry for
  `npm@12.2.3` fixes every byte that runs.
- **Considered:** the bundled npm (the advisories); `devEngines` in `package.json`, which npm checks but does not
  install; Corepack, which Node 25 and later no longer ship.
- **Chosen:** Mise's npm backend for npm alone, under the alias `npm = "npm:npm"`. Mise already owns the runtimes and
  standalone executables here, and npm is the executable that installs the repository's Node graph. The supply test
  resolves aliases and allows exactly one tool of Mise's npm backend, npm itself, so every other Node tool stays in the
  npm lock. A passing `npm ci` does not tell the two npm versions apart, so governance runs `tools.quality.npm_supply`
  first: it compares `npm --version` with the version that `.config/mise/config.toml` selects and refuses any other,
  on the owner's host and in every source job of both Forges.
- **Applies to:** `.config/mise/config.toml` (and its `min_version`, raised to 2026.10.25 with the reason above),
  `.config/mise/mise.lock` and the folder `.config/mise/locks/` that Mise writes for it (owned by the responsibility
  map's `toolchain` role, and left out of the formatters' inputs beside OpenSpec's archive, because Mise checks its
  bytes against their digest), `.config/ci/graph/toolchains.cue`, `tools/quality/npm_supply.py`,
  `tools/quality/governance.py`, `.config/npm/npmrc`, `tests/forge/workflows/test_supply.py`,
  `tests/quality/test_responsibilities.py`, `CONTRIBUTING.md`.

### npm's notices stay out of successful runs

- **Rests on:** npm 12 prints two `npm notice` lines on stderr for each `npm exec`; Node's bundled 11.20.0 printed
  none, and the ci-diagnostics requirement keeps successful output quiet.
- **Chosen:** `loglevel=warn` in `.config/npm/npmrc`, the one npm configuration that every run here reads; warnings and
  errors still show. Measured on this host: with it, `npm exec` prints nothing on success.

## Risks / Trade-offs

- The supply tests select jobs by what they run, not by name: every GitHub job with a step that starts npm needs the
  Mise action, with npm installed, before that step and no `actions/setup-node`; every GitLab job that starts npm needs
  `mise install --locked` before it and npm among Mise's tools. The fresh review's five breaking cases each fail them
  now. A command that starts npm by another spelling than `npm …` or `mise exec … -- npm …` is not seen; the governance
  check is the second line.
- Both Forges run Mise 2026.11.8; this lane measured Mise 2026.11.13 on the host. The governance check on the Forges is
  what shows that the pin holds there.
- An npm update is a supply change that moves `npm` in `.config/mise/config.toml` and runs `mise lock`, which writes
  the new lock folder and digest. The supply test refuses a lock folder that `mise.lock` does not name, so the old
  version's folder is removed in the same change. Whether Renovate's `mise` manager proposes the aliased tool is not
  measured.
- The GitHub `python-quality` job's Mise action installs every declared tool, npm now among them, though it runs no
  npm; Node was installed there already.
- On the owner's host the global configuration still adds its own tools, because a project `miserc.toml` cannot
  exclude it. This Change does not depend on that isolation: the project's own pin wins for npm, and the governance
  check would refuse another npm. `.config/miserc.toml`'s comment now says what the file does.
