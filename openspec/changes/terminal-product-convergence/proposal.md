## Why

The repository delivers useful behavior, but its current shape is not a
coherent terminal product. Product, release, test, quality, documentation, and
Forge concerns have accumulated parallel owners, flat suffix-based modules,
platform exceptions, duplicated configuration, stale residue, and proof that
does not always match the claim it is used to support. Local repairs have
therefore produced churn without removing the causes of recurring failures.

This Change makes the product converge as one system. Existing files and
mechanisms are inputs to evaluate, not defaults to preserve; the Impact names
what left its scope on 2026-11-14.

## What Changes

- **BREAKING** Replace flat, suffix-differentiated and cross-owned source,
  test, and tool layouts with semantic packages and one-way dependencies;
  delete superseded entrypoints, aliases, compatibility layers, and orphaned
  carriers rather than preserving their paths.
- Preserve the Proxy as a narrow local Responses data plane. Client selection,
  credentials, provider choice, conversation history, and control-plane
  projection remain external; no AIGW-specific concept enters the product.
- Converge request admission, replay classification, provider-portable
  projection, transport, diagnostics, and recovery on one semantic owner for
  each contract, with bounded and truthful error output.
- Make install, status, doctor, reload, rollback, recover, upgrade, and
  uninstall one transactional, symmetric, cross-platform lifecycle with exact
  resource ownership and no persistent test or runtime residue.
- Correct the native no-Python acceptance model so it preserves the host
  execution substrate, isolates all Proxy-owned state, and uses the same
  semantic owner on macOS, Linux, and Windows.
- Replace scattered quality rules with one positively declared repository
  model covering source, tests, tools, configuration, specifications,
  documentation, CI projections, generated assets, and root files.
- Use mature ecosystem tools for linting, formatting, typing, dead code,
  dependency hygiene, links and secrets.
- Give every Work Lane one locked bootstrap without a second toolchain, and
  one dependency proposal policy with one declared release age.
- Make CUE the owner of both Forge CI definitions and check the GitHub and
  GitLab files as its projections.
- Enforce SemVer, signed commits and tags, immutable assets, checksums,
  Changelog continuity, exact installed-runtime evidence, and independent
  verification on both Forges.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-diagnostics`: Make verification, development bootstrap and native platform
  evidence reproducible, with both Forge files as checked projections.
- `process-ownership`: Make every native process, service, child, and teardown
  exact, bounded, and leak-free.
- `product-interface`: Make the public CLI, result model, lifecycle symmetry,
  diagnostics, and optional composition boundary natural and complete.
- `provider-portable-responses`: Converge request grammar, replay handling,
  provider adaptation, transport, and recovery without client-control coupling.
- `quality-boundaries`: Positively declare a role for every tracked path, and
  give each declared quality concern one owner and a stated rationale.
- `release-governance`: Define developer and maintainer integration, checked CI
  projections, SemVer publication and dual-Forge projection.
- `repository-organization`: Make ETHOS publication the only branch projector.
- `runtime-upgrade`: Prove atomic installation, upgrade, rollback, recovery,
  uninstall, and exact native-resource retirement on every supported platform.

## Impact

The product, its tests and tools, the CI projections, release metadata, native
packages and service definitions are in scope. Public behavior may change where current behavior is ambiguous,
duplicated, misleading, unsafe, or incompatible with the terminal product.

Since 2026-11-14 the Change closes on the installed product. Twenty-six open
tasks about repository quality, supply, the CI graph, document normalisation
and host cleanup are out of scope, and no delta statement claims their outcome.
Evidence retention stays with the repository family's rules, so
`evidence-layout` is no longer modified. Each task has a destination; one in
ETHOS is this repository's reading of that shared base, and a task that its
destination does not take returns as a new Change. Two Forge secret names keep
the former product name until their values are next set. After the Change
closes, CI moves to a released ETHOS when one can trust commits in CI; that is
ETHOS's schedule, not a task here.

| Task | Subject                                   | Destination                                                                          |
| ---- | ----------------------------------------- | ------------------------------------------------------------------------------------ |
| 6.6  | Carrier audit for one owner per meaning   | ETHOS's repository structure rule; nothing further here                              |
| 6.7  | Unused dependencies and dead code         | ETHOS's shared hygiene reports; removals follow them                                 |
| 7.4  | A mature tool for every concern           | ETHOS's shared base for secrets, vulnerabilities, hygiene and workflow checks        |
| 7.7  | Warnings fatal in every consumer          | Not pursued in maintenance; fatal today in tests, Nox sessions and type checks       |
| 7.10 | Proof that each control takes effect      | ETHOS's shared base for the reach of each obligation                                 |
| 7.11 | Responsibility map against real coverage  | Duplicate checkers leave with the shared base; the map stays here                    |
| 8.2  | Clean Work Lane proof                     | Not pursued in maintenance; every lane start runs the locked bootstrap               |
| 8.3  | No ambient state in success paths         | Not pursued in maintenance; ETHOS states the environment of proof gates              |
| 8.4  | Audit of direct dependencies              | The recurring supply refresh of the repository's owner                               |
| 8.7  | SBOM, licence and provenance outputs      | ETHOS's shared release operation                                                     |
| 8.8  | PyInstaller against its alternatives      | Dropped in maintenance; reopened by a measured defect                                |
| 9.1  | One CI graph in CUE                       | ETHOS's shared CI template                                                           |
| 9.3  | Evidence on every integration path        | ETHOS's shared CI template                                                           |
| 9.4  | Independent jobs without duplicate runs   | ETHOS's shared CI template                                                           |
| 9.5  | Evidence reuse across runs                | ETHOS's shared CI template and its gate-reuse design                                 |
| 9.9  | Both projections for the exact candidate  | Merged into 13.6                                                                     |
| 11.2 | Documented commands on the released build | Merged into 13.7                                                                     |
| 11.3 | Document normalisation                    | Delivered by the shared formatting and layout base; nothing further planned          |
| 11.5 | Each configuration field at its owner     | ETHOS's shared base and its repository structure rule                                |
| 11.7 | One public vocabulary                     | Not pursued in maintenance; reopened by a stale term that a user meets               |
| 12.1 | Benchmarks and budgets                    | The declared budgets are 4.7's measure; lifecycle timing and other platforms dropped |
| 12.2 | Hot-path profiling                        | Dropped unless 4.7 misses a budget                                                   |
| 13.1 | Deletion of superseded entities           | The product part merged into 5.8; duplicate checkers leave with the base             |
| 13.2 | Orphaned host residue                     | Continuous housekeeping of the repository's owner                                    |
| 13.3 | Proposal and lane retirement              | Each lane's closeout                                                                 |
| 13.4 | Failed tags, drafts and Forge records     | The repository's history rebuild, which its owner schedules                          |

Codex transcripts, client configuration, client-selected models, external
Provider credentials, and AIGW source remain outside this repository's write
authority. ETHOS continues to own generic repository lifecycle; this Change
consumes its public governance surface and does not copy its state machine.
