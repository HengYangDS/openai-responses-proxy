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
adapters read all three. Within GitLab, one step list (`#compatibilitySteps`) holds the job's own steps: the Linux job
runs it after its system bootstrap, and the macOS job runs each step through `mise exec --locked --`. The tools that
the macOS job enables are `#Toolchains.compatibility`, from which the quality tool list starts.

**D2. Inheritance and local state.** The job sets `inherit: {default: false, variables: false}`, its own target
(`OPENAI_RESPONSES_PROXY_CI_TARGET: macos-arm64`), its own uv cache directories, and Mise's data, cache and state
directories under `$CI_PROJECT_DIR/.cache/mise`, so no Linux value reaches it and nothing that it runs comes from or
goes to the runner account's home. Its cache derives from GitLab's default cache: the same paths and Mise's directory,
and the default key followed by `$PYTHON_VERSION`, so each matrix job keeps its own interpreter cache, as the accepted
"Hosted setup is deterministic and contention-free" asks of every macOS matrix job. Its Python installs are therefore
repository-local, as "Supply-chain pins are current and reproducible" asks. Whether the two runners are separate system
accounts that cannot write each other's build and cache directories is ETHOS 仓库纳管's to read; the coordinator asked
it on 2026-11-26.

**D3. Interpreters.** The job takes uv and the development Python (3.14) from the repository's Mise lock; `mise` is on
the runner shell's PATH and the guest reaches the network (ETHOS 仓库纳管 read a real DDWG job on the same guest at
2026-11-26 00:1x). On GitLab's macOS shell runner the runner supplies Mise: the guest's one shared Homebrew Mise,
which both proxy runner accounts run (`/opt/homebrew/bin/mise`, 2026.11.12 when ETHOS 仓库纳管 read it at 2026-11-26 05:47,
2026.11.14 after it upgraded the guest at 05:50 on the owner's word, read back 05:51). The
repository's `min_version` in `.config/mise/config.toml` bounds it below, and Mise itself refuses a configuration whose
`min_version` is above its own release (measured locally on 2026-11-26 with a floor of 2099.1.1: `mise install` failed
with "mise version 2099.1.1 is required"). GitHub's mise-action and GitLab's Linux Mise image run the declared release,
`#Toolchains.miseVersion`, which this job does not read. The job's first step, `mise --version`, records in the log
which Mise ran. Whatever Mise runs, every tool comes from the lock with its checksums: Mise checks a tool's lock
checksum when it downloads it; with its data inside the job's cache, a cache miss downloads afresh and a hit restores
what an earlier job of the same cache stored. Mise's lock holds no 3.12 or 3.13, so the matrix version comes from `uv
python install`, as on GitLab's Linux job; GitHub's macOS job takes it from `actions/setup-python` instead. The job runs
`mise install --locked` on the repository's configuration with no trust step, as GitLab's Linux source job does with the
image's Mise in every merge-request pipeline into `dev` (pipeline 18208 passed, 2026-11-26 01:25, the archived Change's
task 10.2). Whether the guest's Mise needs no trust step either, and how long installing 3.12 and 3.13 takes there, are
not yet measured; the first proposal pipeline measures both. Alternatives rejected: an exact version check, because it
fails at every Homebrew upgrade of the shared guest and is inline shell, which the repository paradigm refuses; a
runner-owned pinned Mise as aigw-cli's macOS runners have, because it is a guest change for the owner, and the CI and
runner paradigm across ETHOS repositories is being designed by ETHOS 产品构建.

**D4. Required at once.** The job is not allowed to fail. Alternative rejected: allow failure until a first pipeline
measures it, then flip; that costs a second proposal round, and a failure of the first pipeline is read and fixed in
this lane either way.

## Risks / Trade-offs

- The guest's Python installs may fail or be slow → the first proposal pipeline shows it; the lane fixes the bootstrap
  before landing.
- The guest's shared Homebrew Mise moves with every Homebrew upgrade, apart from the release that the other jobs run →
  each job logs the Mise it ran, the repository's `min_version` refuses one below the floor, and every tool still comes
  from the lock with its checksums; a behaviour change of a newer Mise shows in the job's log.
- The new runners have no maximum timeout, like the reference macOS runners → the job declares its own
  `timeout`, the verification contract's compatibility timeout, which GitHub's macOS job reads as well.
- A proposal pipeline runs only the review role → the protected role first runs in the `dev` pipeline after landing,
  which the keeper reads; it is not a task, since it follows this Change's landing.
