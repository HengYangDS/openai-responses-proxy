## Context

The proposal gives the owner's words and the runners' state. Commit `44506faf` of `drop-windows-and-intel-macos`
removed a first form of this job (both roles, the CUE job, the tags and an allow-failure flag); its diff is the starting
point. The platform declaration in `.config/ci/graph/` already names the macOS runner for GitHub and leaves the GitLab
entry for this Change.

## Goals / Non-Goals

**Goals:** the overlap of the two forges on macOS Python verification from one declaration; the job required.

**Non-Goals:** native assets, lifecycle or release assembly on GitLab; any change to the Linux jobs; the runners
themselves (ETHOS 仓库纳管 owns them).

## Decisions

**D1. One declaration.** The platform declaration gains `macos.gitlab` with the two tag pairs by role; the GitLab
adapter renders the macOS Python job from it and from the same job declaration that GitHub's macOS Python job uses.
No tag literal appears in an adapter. The job declaration is the verification contract's `compatibility` entry in
`.config/ci/graph/proof.cue` (its Nox session and its timeout), the runtime matrix and `#CompatibilityPlatform`; both
adapters read all three.

**D2. Inheritance.** The job sets `inherit: {default: false, variables: false}`, its own target
(`OPENAI_RESPONSES_PROXY_CI_TARGET: macos-arm64`), its own uv cache directories and a cache key per target, so no
Linux value reaches it.

**D3. Interpreters.** The job takes uv and the development Python (3.14) from the repository's Mise lock; `mise` is on
the runner shell's PATH and the guest reaches the network (ETHOS 仓库纳管 read a real DDWG job on the same guest at
2026-11-26 00:1x). Mise's lock holds no 3.12 or 3.13, so the matrix version comes from `uv python install`, as on
GitLab's Linux job; GitHub's macOS job takes it from `actions/setup-python` instead. `mise install` and `mise exec`
trust the repository's configuration on their own (Mise's `trust` help), so the job needs no trust step. Installing
3.12 and 3.13 there is not yet measured; the first proposal pipeline measures it.

**D4. Required at once.** The job is not allowed to fail. Alternative rejected: allow failure until a first pipeline
measures it, then flip; that costs a second proposal round, and a failure of the first pipeline is read and fixed in
this lane either way.

## Risks / Trade-offs

- The guest's Python installs may fail or be slow → the first proposal pipeline shows it; the lane fixes the bootstrap
  before landing.
- The new runners have no maximum timeout, like the reference macOS runners → the job declares its own
  `timeout`, the verification contract's compatibility timeout, which GitHub's macOS job reads as well.
- A proposal pipeline runs only the review role → the protected role first runs in the `dev` pipeline after landing,
  which the keeper reads; it is not a task, since it follows this Change's landing.
