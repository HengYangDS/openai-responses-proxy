## Context

Four resolvers can each hold a release age of their own: Mise (`[settings] minimum_release_age`), Renovate (the
policy's root `minimumReleaseAge`), uv (`exclude-newer`) and npm (`min-release-age`). On `dev` only Renovate's is
written, at zero hours, and a test refuses an age in uv's or npm's configuration so that no second number exists.
The age is now 24 hours for every resolver, so three more places need it.

## Goals / Non-Goals

**Goals:** one authored number; every resolver holds it in its own setting; a later change of the age is one
edited line and one command.

**Non-Goals:** a release-age reader of ETHOS, which does not exist yet; a maintenance run of Renovate; any
dependency's version.

## Decisions

### The declaration is Mise's own setting

- **Rests on:** `mise settings ls` in this lane names the lane's `.config/mise/config.toml` as the source of
  `minimum_release_age` once the line is written (read 2026-11-21). The ETHOS session measured on 2026-11-18 with
  Mise 2026.11.9 that the setting filters neither an exact version nor a version in `mise.lock`, which is every
  tool of this repository. The setting has carried this name since jdx/mise#9384 (merged 2026-04-25), before this
  repository's `min_version`.
- **Considered:** Renovate's root value as the declaration, as on `dev`; a new file that holds the hours; a Mise
  `[vars]` entry.
- **Chosen because:** the tool configuration is where a maintainer already sets the toolchain, and ETHOS's draft
  reader names the same line as its source, so this repository's fill can give way to it without moving the value.
  Mise applies the setting to unpinned resolutions only, of which this repository has none, so the three filled
  settings enforce the number, not Mise.
- **Applies to:** `.config/mise/config.toml`.

### The other three are filled, not compared

- **Rests on:** the owner's design rule of 2026-11-20. Measured in a scratch copy on 2026-11-21: uv 0.13.8 with
  `exclude-newer = "24 hours"` reports the existing lock as out of date, and `uv lock` then adds the four lines of
  an `[options]` table and moves none of the 51 packages; npm 12.2.3 with `min-release-age=1` resolves the lock
  again without a changed line.
- **Considered:** Mise `[env]` entries (`UV_EXCLUDE_NEWER` and npm's environment form), which reach uv and npm only
  under Mise and leave Renovate out, so two mechanisms; a test that compares four authored numbers.
- **Chosen because:** one function gives each resolver's value for a number of hours. The fix command writes that
  value in the resolver's own line, reads the text again as the resolver does and writes nothing unless it holds
  the value. The check reads each file the same way and compares with the same rendering, so no second number is
  authored and the check cannot drift from the fill.
- **Applies to:** `tools/quality/release_age.py`, the `fix` session and the governance graph.

### Zero leaves uv's and npm's lines out, and npm counts whole days

- **Rests on:** npm's `min-release-age` takes days; uv records a span in its lock, where an age of zero would add
  an option that changes nothing.
- **Considered:** writing zero in both; rounding npm's days down.
- **Chosen because:** an absent line is each resolver's own way to hold no age. Rounding up never takes a release
  that the declared hours would hold.
- **Applies to:** the three renderings in `tools/quality/release_age.py`.

### The fix session refreshes uv's lock every time

- **Rests on:** the scratch measurement above: on a lock that agrees with its manifest and its age, `uv lock`
  resolves from the lock without the network and writes nothing.
- **Considered:** refreshing only when uv's line changed in this run, which leaves a stale lock behind a refresh
  that failed; leaving the refresh to a maintainer who reads uv's message.
- **Chosen because:** the session then has one outcome, a lock that agrees with the manifest and the age, and
  nothing to remember after a failed run.
- **Applies to:** the `fix` session of `noxfile.py`.

### The hard-coding policy names the filled files under the dependency proposal control

- **Rests on:** the hard-coding checker admits one control per owning file
  (`hard_coding_owner_reused`), and `.config/mise/config.toml` already owns the workflow executor's pins.
- **Considered:** a control of its own for the release age, which the checker refuses for that owner; a file of
  its own under Mise's `conf.d`, which ETHOS's draft reader does not name; loosening the checker.
- **Chosen because:** the proposal control already lists the Mise configuration among its projections, and the
  age is part of what that policy holds. The check that keeps the three files equal to the declaration is the
  release-age module's, so the policy only has to name where the value appears.
- **Applies to:** the `dependency-proposals` control of `.config/checks/hard-coding/policy.toml`.

## Risks / Trade-offs

- uv applies its age to every resolution under this configuration, a build's requirements included. A release
  younger than the age is then unavailable to a build too, which is the rule.
- uv and npm know no advisory, so a security fix younger than the age is taken in that fix's own change: uv by
  naming the package in `exclude-newer-package`, npm by resolving that package with the age set aside for the one
  command. Renovate's security rule proposes it without the age, as before.
- Renovate adds its 30-minute buffer above zero and holds a release whose registry publishes no time; both are
  described in the governance document already.
- A release that `pyproject.toml` pins with `==` and that is younger than the age leaves `uv lock` without a
  solution, because `exclude-newer` filters the pinned release (uv's documented filtering; not measured here, since
  it needs such a release). The fix command has filled the three files by then. It can run again once the pin has
  aged or is exempted: `uv run --locked --no-sync`, which starts it, does not check the lock (measured in this
  lane on 2026-11-21 with uv 0.13.8, under an age that the lock did not hold).
- npm counts whole days, rounded up. At an age that is not a multiple of 24 hours npm therefore holds a release
  longer than the declared hours, which Renovate's proposal follows; at a multiple, as at 24, its days are those
  hours.
- uv's exemption of single packages for a security fix, `exclude-newer-package`, has to name every young
  dependency that the fix needs, and it stays until those releases have aged. Measured in this lane on 2026-11-21
  with uv 0.13.8, offline: an entry that gives a package a time or a span of its own is part of what uv checks
  its lock against, so adding or removing it fails `uv lock --check` until a refresh; an entry of `false`, which
  only sets the age aside for that package, leaves the lock's options as they are, and `uv lock --check` passes
  with or without it. What a refresh does to a still-young release whose exemption is gone was not measured.
- The fill writes a file with line feeds and one final newline, as the repository's text layout asks of every
  tracked text file; a file that held other line endings or trailing blank lines is brought to that form.
- uv also reads an age that one index holds of its own (`[[index]]` with `exclude-newer`); the check names it as
  another rule, at any depth of uv's configuration.

## Migration Plan

One commit carries the declaration, the three filled lines and the refreshed lock. To go back, the declaration
returns to its earlier value and `mise run fix` runs.

## Open Questions

- The active Change `terminal-product-convergence` says that the proposal policy declares the age. The policy
  still holds one root age for every update that it proposes; where that number is authored moves here. That
  sentence is edited in that Change's own lane.
- The same Change also requires tests to fail while "a package manager's own setting holds or admits releases by
  another rule". uv's exemption for a security fix is such a setting, and it is admitted here by name.
- Nothing yet removes an exemption once its release has aged. Owner: the AIGW and Proxy session. Next action: let
  `mise run fix` drop an aged exemption when the first exemption exists. Review: 2026-12-05.
