## Context

The proposal gives the gap. Three facts bear on the design, read on 2026-11-24 in this lane:

- `mise exec --locked -- npm --version` prints 12.2.3 on the owner's host. `mise ls --current` names its source as
  `~/.config/mise/config.toml`, the operator's global configuration; the project selects no npm. Node 26.13.1's own
  `lib/node_modules/npm` is 11.20.0.
- `.config/miserc.toml` lists the global configuration under `ignored_config_paths`, yet
  `mise settings get ignored_config_paths` and `mise settings get ceiling_paths` both read `[]` (Mise 2026.11.13), and
  `mise config ls` lists the global file. AIGW's CONTRIBUTING records the same limit: a project `miserc.toml`
  "cannot retroactively exclude global or system configuration read at startup".
- AIGW pins the same npm the same way (`[tool_alias] npm = "npm:npm"`, `npm = "12.2.3"`, an integrity lock under
  `.config/mise/locks/npm/`), and its hosted jobs on both Forges pass with it.

## Goals / Non-Goals

**Goals:** one npm, selected and locked by the repository, on every Forge job and checkout.

**Non-Goals:** moving OpenSpec or Prettier into Mise (the 2026-08-29 Change `deterministic-node-toolchain` gave them
one owner, the npm lock); editing the operator's global Mise configuration; repairing the `miserc.toml` isolation.

## Decisions

### npm is a Mise tool, locked by integrity

- **Rests on:** the facts above; npm's own package ships its dependencies bundled, so one integrity entry for
  `npm@12.2.3` fixes every byte that runs.
- **Considered:** the bundled npm (the advisories); `devEngines` in `package.json`, which npm checks but does not
  install; Corepack, which Node 25 and later no longer ship; Mise's npm backend for npm alone.
- **Chosen because:** Mise already owns the runtimes and standalone executables here, and npm is the executable that
  installs the repository's Node graph; the alias keeps the tool's name `npm`, so the existing refusal of `npm:` tool
  keys still keeps every repository tool in the npm lock. AIGW runs the same form on both Forges.
- **Applies to:** `.config/mise/config.toml`, `.config/mise/mise.lock` and the folder `.config/mise/locks/` that Mise
  writes for it (owned by the responsibility map's `toolchain` role, and left out of the formatters' inputs beside
  OpenSpec's archive, because Mise checks its bytes against their digest), `.config/ci/graph/toolchains.cue`,
  `tests/forge/workflows/test_supply.py`.

## Risks / Trade-offs

- GitHub's Mise action installs every tool of the configuration; GitLab's job installs only `MISE_ENABLE_TOOLS`, so
  the source job's list names npm. A job that installs the Node tools without Mise would still run the bundled npm;
  the supply test reads that both Forges' install steps run after Mise.
- On the owner's host the global configuration keeps adding its own tools, because the project's `miserc.toml` does
  not exclude it (measured above). This Change does not depend on that isolation: the project's own pin wins over the
  global one for npm. Making the isolation true is a separate Change.
