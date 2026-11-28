# Bind the ETHOS release

## Context

- Hosted commit admission is the only CI use of ETHOS. GitHub runs it on `ubuntu-24.04`; GitLab runs
  `commit-admission` and `commit-admission-accepted` on its Linux runners in the default uv image. Each job clones
  ETHOS at `92253d2a8`, prepares its Node and Vale, builds the wheel, binds commit trust with a script from ETHOS's
  source tree and runs `ethos hook commit-range` (`.config/ci/graph/proof.cue`, `#EthosSupply`).
- Locally the repository declares no ETHOS release; ETHOS reports `runtime_pin_absent` and runs the build selected in
  `<git-common-dir>/ethos/runtime/CURRENT`, 0.2.0-beta.11.
- ETHOS 0.2.0-beta.12 defines the selection: a bare `"github:HengYangDS/ethos" = "<version>"` under `[tools]` with
  `locked_verify_provenance = true`, written by `ethos lane repair-paradigm --pin` run from that release, then
  locked with `mise lock`. ETHOS follows only this form forward when it selects the local runtime. The older
  `[tools."http:ethos"]` table is read only and verifies a checksum only. CI runs `mise exec --locked -- ethos`, and
  `ethos hook bind-trust --apply` binds the operator's commit trust.
- Where a repository has a runtime selection (`<git-common-dir>/ethos/runtime/CURRENT`), every ETHOS command except
  `hook install` and `release accept` re-executes in the selected runtime, so the successor is selected first with
  beta.12's `ethos hook install --runtime <path> --reason <text>`. A fresh CI checkout has no selection, so hosted
  admission runs the console that Mise installs.

Facts measured on 2026-11-28:

- Adding the pin and running `mise lock` adds only ETHOS entries for the five lock platforms, each with the
  provenance `github-attestations`. The lock format stays at version 2, and the checksums equal the release's
  `SHA256SUMS`.
- `mise install --locked` without a GitHub token downloads the archive by its release URL. It makes one
  unauthenticated attestation request to the GitHub API and verifies the attestation.
- Without `locked_verify_provenance`, Mise trusts a lock entry that records a checksum and a provenance and does not
  verify the provenance again at install; with it, every install verifies (Mise 2026.11.14's settings). It applies to
  every entry that records provenance: actionlint, Python, uv and ETHOS. ETHOS's distribution specification requires
  it in an adopter's pin.
- The Linux ARM64 archive is 361,732,204 bytes; this host downloaded it in 21 seconds.
- The Mise Debian image carries CA roots, curl, Git and `libatomic1`, but no `openssh-client`, and runs as root.
- Every runner runs Mise 2026.11.14: GitLab's image, GitHub's action, and the macOS runner's own Mise, which its
  job records.

## Goals / Non-Goals

**Goals:** one ETHOS release, declared once, runs hosted commit admission and local governance; CI installs it from
the locked release instead of building it.

**Non-Goals:** changing what commit admission checks; running other ETHOS commands in CI; the Python 3.14 move,
which binds 0.2.0-beta.13 in its own Change.

## Decisions

### D1. ETHOS's own pin, written by ETHOS

The pin is the GitHub form, written by `ethos lane repair-paradigm --pin` from 0.2.0-beta.12 and locked by
`mise lock`. Alternatives:

- **The `http:ethos` table**, as two other adopters hold: it verifies a checksum only, and ETHOS does not select
  the local runtime from it. Rejected.
- **A newer source commit**: it keeps a second ETHOS supply beside the release and rebuilds Node and Vale in every
  job. Rejected.
- **A download with its own checksum**: it adds a second lock beside Mise's. Rejected.

### D2. Commit admission installs the locked release

Both Forges' commit-admission jobs enable only the ETHOS tool (`MISE_ENABLE_TOOLS`, as GitLab's Mise jobs already
do), install it from the lock, add the Linux packages that ETHOS's distribution expects (Git, `libatomic1`,
`openssh-client`) and run the pinned console through Mise:

    mise install --locked
    mise exec --locked -- ethos hook bind-trust --apply --root . --json
    mise exec --locked -- ethos hook commit-range ...

The commit trust preflight still runs first. The source checkout, the wheel build, `PYTHONPATH`, the commit-root
variable and the source-tree trust script are removed.

- **GitHub** sets up Mise with the action that the repository already pins. The job passes the ETHOS tool as the
  action's install arguments, which also key its cache apart from the jobs that install the other tools.
- **GitLab** runs both jobs in the Mise Debian image that `source-and-governance` already pins by digest. Both those
  jobs keep their Mise installs in the project directory and cache them keyed by the lock, one cache per tool set, so
  each runner downloads and verifies a locked tool once per lock instead of in every job. Commit admission restores
  no other cache; `source-and-governance` also keeps its uv cache and runs npm through Mise, because its installs
  are no longer on the image's `PATH`.
- **Every other Mise job** enables only the tools it declares, so none downloads ETHOS; GitHub's three Mise jobs
  gain that variable. A test that starts Mise again in a child process passes that selection on, as it passes
  Mise's data directory, so the child installs nothing its job left out.
- **Renovate** proposes no change to the ETHOS pin, which moves only as Migration says.

Mise's floor (`min_version`) was held at 2026.10.14 only because ETHOS's source supply ran that Mise. With the supply
gone it rises to 2026.11.14, the release every runner runs; a test keeps it no newer than the Mise of the GitLab
image.

### D3. The local runtime follows the pin

In this Change's lane, beta.12's `ethos hook install --runtime` selects 0.2.0-beta.12 before the pin repair. After
the pin lands on `dev`, ETHOS keeps `CURRENT` on the pinned release.

## Risks / Trade-offs

- **Download size.** An uncached job downloads 362 MB (Linux ARM64) to 383 MB (Linux x64), about 21 seconds from
  this host. GitHub caches the installation per installed set; GitLab caches it per runner and lock.
- **GitHub's anonymous API limit.** On GitLab, each install of a tool whose lock records provenance makes one
  unauthenticated attestation request. With the caches, that happens when a runner first meets a lock: up to four
  for `source-and-governance` and commit admission together, and two for each macOS job whose cache misses. The
  guests share this host's address, and so its limit of 60 requests an hour, with every other repository that
  adopts ETHOS's pin. A job that hits the limit fails closed and passes once the hour resets. A read-only token in
  GitLab's variables would remove the limit; that is a credential decision for the owner, not taken here. On GitHub
  the workflow token covers the mise-action step, which is the only one that installs.
- **Cached installs are trusted.** A runner reuses what an earlier job on the same lock installed, as the macOS job
  already does; GitLab keeps protected and unprotected caches apart.
- **A new ETHOS release governs local proofs.** beta.12 changes several gates' behavior (its CHANGELOG). This
  Change's own proof runs under beta.12 and shows whether the repository conforms. Selecting it is Git-common: the
  root checkout and every lane of this repository run beta.12 from the selection on.
- **Not exercised here.** Tag-push admission under beta.12 runs first at the next release. If GitLab also exports
  `ETHOS_COMMIT_TRUST_ANCHOR`, `hook bind-trust` refuses two inputs as ambiguous; the first landing shows it.

## Migration

The pin and the CI change land in one commit range. A later release moves through
`ethos lane repair-paradigm --pin`, run from that release, and `mise lock`.
