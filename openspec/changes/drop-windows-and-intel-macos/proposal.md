## Why

On 2026-11-25 the owner withdrew Windows support from every repository that ETHOS governs, from CI and every other
aspect (13:45, relayed); GitLab runs only ARM macOS and, if needed, ARM Linux; GitHub runs ARM macOS and, if needed,
ARM and x86 Linux; both forges name their macOS runner `macos-latest` (13:54, relayed; first-hand 14:52). At 14:52 the
owner confirmed that Windows leaves this repository entirely and that Intel macOS goes too. Each project holds with no
remote, one remote or two, local first, and where the two forges overlap their declaration is one mirrored declaration
(13:47, relayed).

The proxy builds, tests, accepts and documents Windows today: a Task Scheduler supervision adapter, Windows branches in
about twenty lifecycle and serving modules, a `python-windows` job and Windows legs of three GitHub matrices, a
`windows-x86_64` release asset, Windows-only lock entries, and Windows requirements and scenarios in five accepted
specifications. GitHub's macOS runner is pinned to `macos-26`. GitLab runs only Linux ARM64 jobs, so the two forges do
not mirror each other on macOS. The inventory (2026-11-25) is kept at
`build/evidence/proxy-windows-removal-inventory-20261125.md` in aigw-cli's main checkout.

## What Changes

- **BREAKING** The proxy supports macOS ARM64 and Linux only. Releases publish `macos-arm64` and `linux-x86_64`; no
  Windows asset. The supervision adapters are launchd and systemd; Task Scheduler leaves with every Windows branch,
  constraint, test, fixture and lock entry. A seam with one remaining implementation collapses into it.
- CI: the one CUE graph declares one native macOS job and the Linux jobs. GitHub runs macOS on `macos-latest`. GitLab
  gains the same macOS job on its ARM64 macOS guest (tags `macos-latest` with `review` or `protected`), declared once
  and rendered to both forges.
- The open Change `terminal-product-convergence` drops the Windows parts of its open tasks and deltas in this same
  Change, so that its archive cannot bring Windows back.
- Documents, decision records and accepted specifications say the same. The release is 7.0.0.

## Capabilities

### Modified Capabilities

- `ci-diagnostics`, `runtime-upgrade`, `product-interface`, `process-ownership`, `quality-boundaries`: no Windows
  platform, job, asset, adapter or scenario; GitLab and GitHub mirror the macOS job.

## Impact

`src/openai_responses_proxy` (lifecycle, supervision, serving, identity), about 53 test files, `.config/ci/` and the
generated pipelines, `.config/mise/`, `uv.lock`, coverage and lint configuration, documents, the five specifications
and the open Change `terminal-product-convergence`. Out of scope: a new Linux ARM64 asset (not asked; it would widen
the Change); the CI guests and runner registration (ETHOS 仓库纳管); the CI and runner paradigm that 97f828 designs for
every governed repository (its design reaches the keeper before it touches this repository).

## To settle in the design

- How the published-release compatibility check treats a predecessor (6.0.3 and earlier) that carries a Windows asset:
  verify only the platforms the current product declares, without reading the extra asset as an error.
- How uv and Mise narrow their lock platform sets, measured with the tools themselves.
- Whether `docs/evidence/windows-real-machine-validation.md` stays as dated evidence or leaves.
