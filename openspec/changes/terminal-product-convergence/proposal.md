## Why

The repository delivers useful behavior, but its current shape is not a
coherent terminal product. Product, release, test, quality, documentation, and
Forge concerns have accumulated parallel owners, flat suffix-based modules,
platform exceptions, duplicated configuration, stale residue, and proof that
does not always match the claim it is used to support. Local repairs have
therefore produced churn without removing the causes of recurring failures.

This Change makes the entire repository converge as one system. Existing files
and mechanisms are inputs to evaluate, not defaults to preserve: every retained
entity must have one precise meaning, one owner, one reason to change, a current
consumer, and evidence that it contributes to the terminal product.

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
- Use mature ecosystem tools where they reduce total complexity; retain custom
  checks only for repository-specific semantics that no upstream tool owns.
- Make the locked development environment reproducible per Work Lane, share
  only content-addressed caches, and upgrade every direct tool and dependency
  to its verified current stable release without creating a second toolchain.
- Make CUE the owner of both Forge CI definitions and check the GitHub and
  GitLab files as its projections.
- Enforce SemVer, signed commits and tags, immutable assets, SBOM, provenance,
  checksums, Changelog continuity, exact installed-runtime evidence, and
  independent verification on both Forges.
- Rebuild documentation, decisions, examples, links, configuration, and
  OpenSpec history around the current product; remove empty evidence shells,
  stale records, obsolete warnings, misleading examples, and unconsumed files.
- Delete merged proposal branches, retired Work Lanes, invalid hooks, orphaned
  services and processes, temporary artifacts, obsolete release assets, and
  every historical residue that has no current truth or recovery consumer.

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
  projections, SemVer publication, dual-Forge projection, and terminal branch
  cleanup.
- `repository-organization`: Make logical and physical organization isomorphic,
  navigable, minimal, and reproducible in every Work Lane.
- `runtime-upgrade`: Prove atomic installation, upgrade, rollback, recovery,
  uninstall, and exact native-resource retirement on every supported platform.

## Impact

The complete tracked repository and its produced artifacts are in scope:
`src`, `tests`, `tools`, `.config`, root configuration, OpenSpec, documentation,
CI generation and projections, release metadata, dependency locks, native
packages, service definitions, branch and tag lifecycle, and owned host-local
runtime state. Public behavior may change where current behavior is ambiguous,
duplicated, misleading, unsafe, or incompatible with the terminal product.

Since 2026-11-14 the Change closes on the installed product. Twenty-six open
tasks about repository quality, supply, the CI graph, document normalisation
and host cleanup left it; the design names each with its destination, and no
delta statement claims their outcome. Evidence retention stays with the
repository family's rules, so `evidence-layout` is no longer modified.

Codex transcripts, client configuration, client-selected models, external
Provider credentials, and AIGW source remain outside this repository's write
authority. ETHOS continues to own generic repository lifecycle; this Change
consumes its public governance surface and does not copy its state machine.
