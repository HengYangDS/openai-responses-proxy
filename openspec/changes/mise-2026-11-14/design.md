## Context

`#Toolchains` in `.config/ci/graph/toolchains.cue` owns the CI supply of both forges. GitLab's Mise jobs start from
`gitlabMiseImage`; `miseVersion` is read from that image's tag, and GitHub's mise-action steps install that version
through the revision in `githubActions.mise`. `tools.ci.project` renders `.gitlab-ci.yml` and
`.github/workflows/verify.yml` from the graph, and the supply tests check the relations (one Mise release on both
forges, a digest-pinned image, a commit-pinned action) without repeating a version or a digest. No local tool reads
these fields.

The floor `min_version` in `.config/mise/config.toml` is a separate fact. Its comment and commit `260290d5` record why it
stays 2026.10.14: ETHOS's CI supply runs its own pinned Mise 2026.10.14 in this repository for commit admission, and a
higher floor made it refuse the checkout on both forges.

## Goals / Non-Goals

**Goals:** both forges run Mise 2026.11.14, the latest stable release, from immutable references; mise-action is its
latest stable release.

**Non-Goals:** the floor, the tools that Mise installs and their lock, and any Mise that a runner provides itself.

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
as verified). Its release notes and those of v5.2.10 list two changes: an MSYS `curl` download fix on Windows, which no
job here runs, and a new optional `mise_env` input that changes nothing when unset. The repository's steps set only
`version`, `install` and `cache`, which the supply test holds.

### D3 The floor stays

Raising `min_version` to 2026.11.14 was rejected for the reason in Context. The CI pin and the floor answer different
questions: which Mise the forges run, and the oldest Mise that may run the repository.

### Release notes read

Mise 2026.11.9 to 2026.11.14 name one breaking change, Temurin hotfix version strings, and this repository installs no
Java. Their lockfile changes concern `mise lock` and task-level tool pins; an install under `locked = true` does not
write the lock. A locked install with Mise 2026.11.14 in the lane measures the last claim (task 2.1).

## Risks / Trade-offs

- **Hosted behavior is unmeasured here.** No local run executes the new image or the new action; the hosted jobs on
  both forges are the first to run them, and a failure there leaves the previous pin one revert away.
- **GitHub and GitLab could diverge** if one pin moved without the other. The derivation in D1 and the supply test that
  compares the two forges keep them one release.
