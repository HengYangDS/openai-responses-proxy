## Context

`#Toolchains` in `.config/ci/graph/toolchains.cue` owns the executors of the repository's own CI jobs on both forges.
GitLab's Mise jobs start from
`gitlabMiseImage`; `miseVersion` is read from that image's tag, and GitHub's mise-action steps install that version
through the revision in `githubActions.mise`. `tools.ci.project` renders `.gitlab-ci.yml` and
`.github/workflows/verify.yml` from the graph, and the supply tests check the relations (one Mise release on both
forges, a digest-pinned image, a commit-pinned action) without repeating a version or a digest. No local tool reads
these fields.

The commit-admission jobs of both forges do not use these fields. They build the ETHOS commit that `#EthosSupply` in
`.config/ci/graph/proof.cue` pins, `92253d2a`, and that commit's `min_version` and `[bootstrap] version` are both
2026.10.14 (read from ETHOS's repository on 2026-11-26), so commit admission runs Mise 2026.10.14.

The floor `min_version` in `.config/mise/config.toml` is a separate fact. Its comment and commit `260290d5` record why it
stays 2026.10.14: the Mise of commit admission refuses a checkout whose floor is above it, and a higher floor made it do
so on both forges.

## Goals / Non-Goals

**Goals:** the repository's own Mise jobs on both forges run Mise 2026.11.14, the latest stable release, from immutable
references; mise-action is its latest stable release.

**Non-Goals:** the floor, the Mise of commit admission (the proposal names its owner and next step), the tools that Mise
installs and their lock, and any Mise that a runner provides itself.

## Decisions

### D1 The image moves by its index digest

The pin is the OCI image index of `ghcr.io/jdx/mise:2026.11.14-debian`, as the 2026.11.8 pin was, so that each runner
pulls its own platform's manifest. The index lists `linux/amd64` and `linux/arm64` manifests, which covers the ARM64
GitLab runners. The digest was read on 2026-11-26 in two ways that agree: the `Docker-Content-Digest` header of an
anonymous ghcr.io registry request, equal to the SHA-256 of the returned index, and
`docker buildx imagetools inspect`. The same registry request returns the digest of the current 2026.11.8 pin, so the
method reads the kind of digest that the graph holds.

Only the image line changes: `miseVersion` and every GitHub `version` input follow from it.

### D2 mise-action v5.3.0 by its commit

The tag `v5.3.0` names commit `924e7c586e33d7d4bae9c14e26c5d22e2beb6e78` (a lightweight tag; GitHub reports the commit
as verified). Three changes since v5.2.9 reach the action:

- v5.2.10's `f620f37d` (jdx/mise-action#744) rewrites the curl download in `requestOnce` on every platform, not only
  for the MSYS curl of its notes: the body always goes to a temporary file, and `--write-out` carries no newline. All
  three steps here run on `ubuntu-24.04` and download Mise through it.
- packslip moves from 1.11.3 to 1.11.4, which its release notes say has no user-facing change.
- v5.3.0 adds an optional `mise_env` input that changes nothing when unset. The repository's steps set only `version`,
  `install` and `cache`, which the supply test holds.

No local run exercises the new download path; the proposal's GitHub run (task 5.1) is its measurement.

### D3 The floor stays

Raising `min_version` to 2026.11.14 was rejected for the reason in Context. The CI pin and the floor answer different
questions: which Mise the repository's own jobs run, and the oldest Mise that may run the repository.

### Release notes read

Mise 2026.11.9 to 2026.11.14 name one breaking change, Temurin hotfix version strings, and this repository installs no
Java. Their lockfile changes concern `mise lock` and task-level tool pins; an install under `locked = true` does not
write the lock. A locked install with Mise 2026.11.14 in the lane measures the last claim (task 2.1).

Mise 2026.11.10 makes `mise x -- <cmd>` run the pinned tool's binary again even when the inherited `PATH` already holds
that tool's directory (jdx/mise#15838). GitLab's `mise exec --locked -- uv …` steps take that path, so the change works
in the repository's favour: they run the locked uv.

## Risks / Trade-offs

- **Hosted behavior is unmeasured here.** No local run executes the new image or the new action; the hosted jobs on
  both forges are the first to run them, and a failure there leaves the previous pin one revert away.
- **GitHub and GitLab could diverge** if one pin moved without the other. The derivation in D1 and the supply test that
  compares the two forges keep them one release.
