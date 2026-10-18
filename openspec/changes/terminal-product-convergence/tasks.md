# Tasks

Historical execution details and original evidence references remain in Git source
`8fea91a7` and native custody Attestation
`38a61490598baec90f7bfed4083615b668c1560dd0ddd3a0d1a0aa933ec7725f`.
This checklist retains the original task identities and completion states.

## 1. Baseline and Authority

- [x] 1.1 Capture the source, lease, Change, installed runtime, selected Forge refs, CI, releases and owned residue; verify the native baseline inventories.
- [x] 1.2 Inventory every tracked file and generated projection by semantic owner, consumer, source-of-truth, reason to change, dependency direction, and retirement condition; verify no file is silently omitted or multiply owned.
- [x] 1.3 Map public commands, results, configuration, routes, resources and artifacts to one invariant and owner; verify complete, unique surface coverage.
- [x] 1.4 Reconcile source, tests, specs, docs, quality, CI and release metadata; verify native checks and keep unresolved contradictions in their existing tasks.
- [x] 1.5 Prove the current installed release remains healthy and unchanged before source migration; save its exact version, executable digest, payload identity, service identity, listener PID, and loopback health as the rollback baseline.

## 2. Windows Native Environment and Immediate Release Safety

- [x] 2.1 Add RED contracts proving one native environment owner preserves arbitrary host execution state, removes inherited Proxy and Python injection state, redirects every product-owned root, and accepts an explicit empty product `PATH`.
- [x] 2.2 Use the sole process-environment owner in the renamed product package; verify its focused contracts on the current host.
- [x] 2.3 Make native release and CLI fixtures use that environment owner; verify the duplicate runners and partial environment policies are absent.
- [x] 2.4 Verify help, version, status, every public command, prewarm, and native lifecycle black-box paths use the same environment contract without Python discovery or host-substrate removal.
- [x] 2.5 Reproduce the former Windows `WinError 10106` boundary in a regression contract and obtain GREEN evidence from the real Windows native artifact job for the exact candidate commit.
- [x] 2.6 Verify macOS and Linux native acceptance remain green and no new service, process, temporary payload, cache, or host configuration survives either successful or failed execution.

## 3. Product Boundary and Public Interface

- [x] 3.1 Define the product domains and their boundaries; verify every public concept has one owner and excludes client configuration and AIGW control.
- [x] 3.2 Audit all CLI commands, options, defaults, exit codes, human output, JSON schemas, and help text against real user journeys; add RED tests for every ambiguous, inaccurate, over-broad, or misleading result.
- [x] 3.3 Converge human and machine output on one typed result model with precise problem, current state, safe next action, and bounded evidence; verify no traceback, warning, private path, credential, payload, or internal module name leaks. Evidence: `cli/outcome.py` owns the admitted result and its one next action for both renderers; leak regressions in `tests/cli`, `tests/serving/handoff/test_state_machine.py` and `tests/relay/transport`.
- [x] 3.4 Verify `install`, `status`, `doctor`, `reload`, `rollback`, `recover`, and `uninstall` are semantically distinct, complete, symmetric, idempotent where declared, and free of hidden source or Forge dependencies. Evidence: lifecycle audit on `7306a1de`; `b3453210` keeps the Windows default roots disjoint (proved by regression, not a native default-root run); `d540e9cc` names only admissible `doctor` actions; the runtime-upgrade delta limits no-op results to the lifecycle lock and adds `recover`'s `purged` result.
- [x] 3.5 Remove duplicate product, command, service and platform identities; verify each remaining identity belongs to its declared owner.
- [x] 3.6 Prove Proxy operates independently with an ordinary explicit upstream route and loopback endpoint; verify AIGW absence, client absence, and Forge absence do not prevent local product operation. Evidence: v5.0.5 isolated native journeys on three platforms and the product import gate.

## 4. Responses and Provider Architecture

- [ ] 4.1 Trace the complete request path from HTTP admission through schema classification, replay relationships, Provider projection, transport, streaming, response validation, and error recovery; verify each transformation has one typed input and output owner. Framing/body budgets are qualified; input recovery now consumes native typed diagnostics and metrics without reconstruction. Complete pipeline acceptance remains open. Evidence: `build/evidence/request-body-budget-20261018`, `build/evidence/typed-recovery-20261018`.
- [ ] 4.2 Merge parallel replay classifiers, provider-shape inference, sanitizers, and recovery predicates into one authoritative item and relationship policy; delete all fall-through and compatibility interpretations.
- [ ] 4.3 Cover malformed and unknown items, ciphertext, tool pairing, Provider-local data, stream control and terminal errors; verify adversarial regressions. Non-finite numbers and non-string selectors fail with bounded protocol reasons; valid bytes and unrelated live content remain preserved. Evidence: `build/evidence/json-number-admission-20261018`, `build/evidence/selector-admission-20261018`.
- [x] 4.4 Ensure Provider-specific wire differences live only in narrow adapters selected from one manifest and policy contract; verify generic relay, lifecycle, CLI, and tests do not branch on Provider names.
- [x] 4.5 Define the low-cost Provider extension path—manifest entry, adapter, policy, contract fixtures, conformance suite, documentation, and no core modification—and prove it with one representative non-default Provider fixture.
- [ ] 4.6 Evaluate mature local-first gateways and protocol libraries against the exact retained differentiators; replace custom generic mechanics only where doing so reduces source, dependencies, runtime risk, and maintenance authority.
- [ ] 4.7 Verify request and response performance, bounded retries, cooldown monotonicity, cancellation, connection reuse, memory, and streaming latency against explicit budgets without weakening correctness or multiplying retries. TLS fixture repair `54d22a68` passed both proposal graphs, including Windows Python 3.12; accepted-ref verification and system DNS cancellation remain unproved. Evidence: `build/verification/54d22a68425d4cb11b73423f1cded8ffaf5eb23c/hosted`.

## 5. Native Lifecycle and Resource Ownership

- [ ] 5.1 Converge install, upgrade, reload, rollback, recovery, and uninstall on one transaction state machine and mutation lock; verify preconditions, durable transitions, and terminal states through failure injection, including same-controller cleanup retry and cross-process recovery after partial directory removal.
- [ ] 5.2 Make payload generation, manifest, command projection, transaction journal, rollback snapshot, service declaration, listener, watchdog, and handoff child each have exact ownership identity and one cleanup owner.
- [x] 5.3 Prove macOS launchd creation and teardown share the exact label and plist path; remove every suffixed test service, process, and plist while preserving the canonical installed service.
- [x] 5.4 Prove Linux systemd behavior on a real supported user service and in the declared container boundary, including explicit behavior when no user bus exists; remove session-only fallback processes.
- [x] 5.5 Prove current-user Windows Task Scheduler install, status, handoff, recovery, rollback, uninstall, command projection, and process-generation ownership from the native artifact.
- [ ] 5.6 Verify bounded teardown after success, assertion failure, exception, timeout, and interruption on every supported platform; compare exact owned services, processes, journals, payloads, commands, and temporary files before and after, preserving unrelated canonical installations.
- [x] 5.7 Verify active-target installation is a true no-op, failed successor transition restores the exact predecessor, repeated recovery is terminal, and uninstall preserves all unowned content.
- [ ] 5.8 Remove legacy payload shapes, alternate launchers, obsolete journals, dead schema readers, compatibility branches, and fallback service identities after the terminal lifecycle proves no consumer.
- [x] 5.9 Preserve request admission during capability-qualified upgrade and rollback handoff; prove concurrent new requests and in-flight responses complete without `proxy_draining`, while retaining the bounded legacy native-generation fallback.

## 6. Semantic and Physical Repository Topology

- [ ] 6.1 Derive the target package map from the product ontology and repository-only domains; verify every package name is precise, non-overlapping, and explains its dependency direction without reading implementation details.
- [x] 6.2 Replace flat suffix families and ambiguous buckets in `src`, `tests`, and `tools` with semantic subpackages; specifically converge `tools/release/publish*`, `publication/*`, and `forge/*` on one publication entrypoint, release construction owner, and Forge adapter boundary. Evidence: one fixture carrier per test package in the quality inventory; `tools/release/publication` publishes, `tools/release/artifact` constructs, and `publication/{github,gitlab}` adapt each Forge. The generic `service` name stays with 6.3.
- [x] 6.3 Remove or precisely rename every ambiguous `common`, `shared`, `utils`, `helpers`, `misc`, `base`, `manager`, generic `service`, concatenated compound, and implementation-shaped module; verify no compatibility import or re-export remains. Evidence: `serving` (`a6c5a91f`), `relay/downstream`, `relay/upstream` and `lifecycle/records` replace the ambiguous names without an alias module or re-export; `supervision/native_service` names the host's native user service.
- [ ] 6.4 Make tests mirror product and repository-tool semantics rather than implementation filenames; keep unit tests with their domain and isolate integration, native, release, and end-to-end contracts by evidence scope.
- [x] 6.5 Establish one-way import rules for product domains and repository tools, detect cycles and undeclared owners, and verify no product package imports tests, repository tooling, Forge code, or ETHOS internals. Evidence: the architecture gate's product edge, undeclared-import, tool-domain cycle and test-import checks.
- [ ] 6.6 Audit `.config`, root files, OpenSpec, docs, schemas, workflows, generated files, and release assets for the same semantic and physical isomorphism; relocate, absorb, rename, or delete every mixed-responsibility carrier.
- [ ] 6.7 Remove empty packages, redirect-only indexes, dead entrypoints, duplicate schemas, parallel constants, obsolete fixtures, unused dependencies, and unreachable code; verify repository size and entity count decrease without losing a required invariant.

## 7. Quality System

- [x] 7.1 Replace scattered quality declarations with one responsibility map; verify every tracked carrier and concern has its declared native owner.
- [ ] 7.2 Consolidate Ruff formatting, imports, correctness, modernization, naming, documentation, exception, logging, security, complexity, pytest, and dead-code rules into one comprehensible authority; enable every applicable rule and justify every inapplicable rule without blanket ignores.
- [ ] 7.3 Make Ty strict at product and repository-tool boundaries, narrow unions and protocols, eliminate avoidable `Any`, and add typed adapters where external data enters; verify no unresolved type warning is accepted.
- [ ] 7.4 Adopt or fully exercise mature tools for dependency hygiene, dead code, security, Markdown format and lint, links, TOML, YAML, JSON, CUE, Actions, secrets, licenses, SBOM, and vulnerabilities; delete custom equivalents that add no unique semantic value.
- [ ] 7.5 Close structural measurement and enforcement together: verify file/function ELOC, logical statements, nesting and complexity semantics; classify the native-rule findings by product, tooling and test behavior; set risk-justified blocking limits, remove the observation-only acceptance contract, and simplify each affected semantic owner without suppressions or shallow extraction.
- [ ] 7.6 Enforce meaningful public/tool API docstrings; verify signatures match implementation and omit ornamental test docstrings.
- [ ] 7.7 Make warnings fatal across tests, builds, docs, tools, native binaries, and CI; remove every known warning at its owner rather than filtering or baseline-suppressing it. Coverage report-only data closure and stable 7.16.2 pass exact-HEAD proof without runtime warnings; all-consumer warning enforcement remains open. Proof: `17d5c638`.
- [ ] 7.8 Verify commit subjects through one scoped Conventional Commit grammar in local hooks and both Forge paths, including generated lifecycle commits, with no duplicate parser or historical exception list.
- [ ] 7.9 Run static, configuration, documentation and affected behavior gates; verify clean output before the full suite.
- [ ] 7.10 Prove control effectiveness with isolated conformance cases for native tool settings, scope inclusion, nonzero exits, local entrypoints and hooks, and every CUE event route; a missing tool, skipped required check, empty scope or stale report must not establish acceptance.
- [ ] 7.11 Reconcile the quality responsibility map with actual coverage, dependency, dead-code, security, documentation and delivery checks; close product/tooling/orchestration coverage independently, remove duplicate custom checkers, and leave unverified claims open.

## 8. Development Environment and Supply Chain

- [x] 8.1 Use mise as the developer entrypoint; verify bootstrap, check, native and release tasks invoke existing ecosystem owners.
- [ ] 8.2 Prove a clean Work Lane reconstructs independent `.venv`, `.nox`, `node_modules`, build, coverage, and temporary state from locks while sharing only content-addressed mise, uv, npm, and Python caches.
- [ ] 8.3 Remove ambient interpreter, user-site, global mise configuration, system package, and another repository environment from local and hosted success paths; verify empty-HOME and empty-project-cache bootstrap.
- [ ] 8.4 Audit all direct dependencies and release inputs against official stable sources; qualify compatible successors and outstanding vulnerability/argv boundaries before final freeze.
- [x] 8.5 Regenerate `mise.lock`, `uv.lock`, and `package-lock.json` deterministically; verify a second resolution is byte-clean and no duplicate version literal controls behavior.
- [ ] 8.6 Configure one dependency update proposal owner with release-age policy, grouping, vulnerability priority, auto-merge criteria, and dual-Forge projection; verify it cannot open competing GitHub and GitLab updates for the same change.
- [ ] 8.7 Produce and verify SBOM, vulnerability, license, checksum, signature, and provenance outputs from the exact locked candidate without embedding checkout paths, timestamps, credentials, or installer metadata.
- [ ] 8.8 Evaluate PyInstaller and current alternatives against startup, size, reproducibility, platform support, lifecycle integration, and maintenance cost; retain or replace it based on measured total value, then delete the rejected path.

## 9. CI and Forge Projection

- [ ] 9.1 Define the quality, Python, native, lifecycle, metadata, publication and parity graph in CUE; verify each job has explicit acceptance facts.
- [ ] 9.2 Generate GitHub Actions, GitLab CI and `.config/ci/python-versions` from the CUE model, verify semantic parity and provider-specific deltas, and reject hand-edited projection drift.
- [ ] 9.3 Cover proposal creation and update, review SHA, maintainer fast-forward, `dev`, `main`, and tag events; verify every admissible integration path triggers the required exact-SHA evidence.
- [ ] 9.4 Separate independent jobs for fast quality, Python 3.12/3.13/3.14 compatibility, macOS, Linux, Windows, release construction, and publication; remove monolithic verification and meaningless duplicate runs.
- [ ] 9.5 Define when exact-SHA evidence may be reused and when platform, environment, source, lock, or release changes require new execution; verify reuse cannot turn stale or partial proof green.
- [x] 9.6 Make native runner availability explicit: GitHub Windows may own current Windows proof, GitLab may omit that projection, and no unavailable runner remains a false permanent blocker or a false success; verify affected native contracts and reference cleanup.
- [ ] 9.7 Verify GitHub and GitLab authentication, SSH agent, author identity, commit signature, tag signature, protected-branch, proposal-branch, and automatic merge behavior without password prompts or private-key mutation.
- [ ] 9.8 Ensure proposal branches are unprotected, merge automatically after required evidence for the maintainer policy, and are deleted on merge; verify no remote `work/*` or stale proposal remains.
- [ ] 9.9 Run both generated CI projections for the exact candidate and verify `dev`, `main`, and the final tag receive the intended green graph with readable, bounded job output. `1c7d6fbb` passed both proposals, GitHub `dev/main`, and GitLab `main`; GitLab `dev` exposed test teardown misuse (fixed by `4cf23722`) while the disk-failed Python job passed one retry. Successor/tag acceptance remains open; current Linux runners are paused. Evidence: `build/verification/1c7d6fbb899545a6c31c649fd45d1da8f8983d0d`.

## 10. Release and Installed Product Proof

- [x] 10.1 Select SemVer from public compatibility and update version, package, Changelog, docs and asset names; verify metadata consistency and integrate the historical-checkout fix with newer shared tags present. Source: `4ade34e5`; repository proof: `88c5dda6`; both peers' review, `dev` and `main` CI passed. Lane retirement remains under 13.3.
- [ ] 10.2 Build reproducible macOS, Linux, and Windows native bundles from the locked candidate; verify contents, modes, manifests, checksums, signatures, SBOM, provenance, and common-platform byte identity where applicable.
- [x] 10.3 Verify release source is clean, signed, exact-HEAD proved and immutable, with current source-acceptance authority, before creating one signed annotated tag object.
- [x] 10.4 Project the same signed commit and tag objects to each selected Forge through independent exact-CAS operations; verify no author, committer, parent, tree, message, or signature rewriting.
- [x] 10.5 Publish complete matching Release inventories on GitHub and GitLab; independently verify byte digests, signatures, trust anchors, metadata, and user-facing link identity from fresh persisted state, including idempotent and concurrent publication paths.
- [x] 10.6 Upgrade the preserved working installation from the previous accepted release, prove runtime health and active-target no-op, roll back, recover, re-upgrade, uninstall, and reinstall using only published artifacts.
- [x] 10.7 Verify installed status reports exact release, payload, manifest, service, process, listener, command projection, and transaction state without consulting source, Git, uv, Nox, ETHOS, or a Forge.

## 11. Documentation, Configuration, and Experience

- [x] 11.1 Rebuild the documentation map around product overview, concepts, setup, operations, architecture, contribution, governance, and decisions; verify every canonical document is reachable from `docs/README.md` and every index has a navigation purpose.
- [ ] 11.2 Rewrite operation and routing journeys against the released executable; verify every documented command in a clean environment.
- [ ] 11.3 Normalize names, headings, terminology, tables, code blocks, internal links, and external links for semantic precision and readable `信、达、雅`; verify Markdown format, lint, table, and link gates are clean.
- [x] 11.4 Use numbered durable Decision Records and a complete register; verify enduring decisions and rejected alternatives have a canonical owner.
- [ ] 11.5 Audit `.editorconfig`, `.gitattributes`, `.gitignore`, `.npmrc`, pytest, pyproject, mise, uv, OpenSpec, ETHOS adoption, release, and quality configuration; make each field reside in its true tool authority and delete stale or duplicate entries.
- [x] 11.6 Retire obsolete examples, private paths, commands, coupling and evidence shells from active reader paths; verify current documentation and links.
- [ ] 11.7 Verify source, config, specs, docs, help, schemas, generated workflows, and release metadata use the same public vocabulary and that a renamed concept leaves no stale reference.

## 12. Performance, Security, and Operational Reliability

- [ ] 12.1 Benchmark startup, steady request latency, streaming first-event latency, memory, concurrency, handoff interruption, install, update, rollback, and recovery using reproducible inputs; establish risk-derived budgets.
- [ ] 12.2 Profile hot paths and remove accidental allocations, repeated parsing, redundant serialization, duplicate network work, polling churn, and unnecessary subprocesses without changing portable semantics.
- [ ] 12.3 Threat-model local listener exposure, upstream credentials, request and response logging, temporary assets, signature trust, update supply chain, native service ownership, and transaction recovery; close every high-severity gap.
- [ ] 12.4 Verify logs are structured, bounded, redacted, rotation-aware, and operationally useful; introduce no logging framework unless it replaces more complexity than it adds.
- [ ] 12.5 Inject network failure, malformed Provider response, interrupted handoff, killed controller, disk error, locked Windows file, stale service, PID reuse, and invalid journal; verify bounded failure and exact recovery without data or host pollution.

## 13. Destructive Cleanup and Terminal Closeout

- [ ] 13.1 Delete every superseded module, test helper, configuration owner, schema, workflow fragment, document, compatibility path, alias, fallback, exemption, baseline, and dependency identified by the ownership inventory; verify no tracked reference or runtime consumer remains.
- [ ] 13.2 Remove exact orphaned test services, processes, plists or service entries, transaction roots, payloads, hooks, caches, bytecode, coverage, build output, temporary files, and stale ETHOS projections without touching user data or foreign state.
- [ ] 13.3 Merge or discard every local and remote proposal according to semantic value, delete merged proposal and remote `work/*` refs, retire obsolete Worktrees and leases, and verify only canonical repository-family members remain.
- [ ] 13.4 Remove failed unpublished tags, draft Releases, duplicate assets, and unreferenced records from both Forges and local storage while preserving formal immutable release history and required recovery evidence.
- [ ] 13.5 Freeze the complete candidate and run strict OpenSpec validation, all focused gates, full quality, Python matrix, three-platform native acceptance, reproducible build, security, performance, documentation, and repository-residue audits exactly once at final scope.
- [ ] 13.6 Complete tracked obligations and land/publish the signed accepted candidate; verify selected refs, tag CI and Releases before the archive follow-up.
- [ ] 13.7 Prove the final installed product and clean repository family satisfy every modified capability, disclose any genuinely unverified external fact, and retain no active Change, Work Lane, proposal branch, temporary authority, or consumerless entity.

## 14. Bounded Responses Compatibility Delivery

- [x] 14.1 Qualify and publish signed/notarized AIGW 0.3.5; verify original Homebrew upgrade, direct rollback and restored forwarding. Historical SSE causes remain unknown.
- [x] 14.2 Refine existing route/client and Proxy replay/collaboration/issuer contracts; verify preview and refusal without a new control plane.
- [x] 14.3 Qualify installed Codex/UCloud, Hermes/DMXAPI and SDK tool/replay/stream journeys; verify supported compaction and independent Proxy operation. SDK server compaction remains unsupported.
- [x] 14.4 Close diagnosed compatibility and runtime gaps at existing owners; verify native acceptance and official upstream handling of reproducible vendor defects.
- [x] 14.5 Deliver the product and repository rename through all active consumers; verify preserved IDs, history, permissions, content and legacy withdrawal.
- [x] 14.6 Publish signed stable selected-platform assets; verify native upgrade, rollback, installed consumers, protected settings and original installation ownership.
- [x] 14.7 Integrate source, release and installed-consumer evidence; verify the delivered increment's proposal, lane and lease are absent. Custody: `9e837e05`, `f719cedb`, `c0f59b51`, `c38a09db`, `1a100ad7`, `e526e585`; retirement: `3ffef5bf`. GitLab catch-up custody: `2778fd09`, `43559872`, `f10232c7`. Hermes 5.0.6 compression was a protected no-op; effective compaction remains versioned 5.0.1 evidence. Historical CI failures remain preserved; their release-history repair is closed under 10.1.
- [x] 14.8 Restore macOS Background supervision at the current owner; verify no-GUI lifecycle, migration, rollback and host preservation.
- [x] 14.9 Align overview, package and selected Forge descriptions with qualified behavior; verify current identity and avoid universal-support claims.
- [x] 14.10 Preserve precise stream/protocol diagnostics and committed output; verify terminal agent-argument proof through focused, released and installed behavior without attributing old unknown failures. Original native journey: custody `9e837e05`, `agent-delivery/native-terminal-journey-formal-result.json`.
- [x] 14.11 Restore macOS supervision after restart; verify GUI-login acceptance and retain the separately deferred no-GUI/GitLab qualification.
- [x] 14.12 Provide a supported reload path for a stopped installation; verify current doctor guidance and released native lifecycle.
- [x] 14.13 Restore a serving installation whose supervisor stopped; verify released reload preserves the listener and native resource ownership.
- [x] 14.14 Report outdated supervision and rebind the installed release's definition; verify native status, doctor and reload contracts.
- [x] 14.15 Restore same-artifact supervision and refuse rollback without a serving runtime; verify full gates, signed release and released three-platform journeys. Released in v5.0.5.

## Workflow follow-up

- Archive this Change through ETHOS after tracked obligations and review are complete.
- Verify archived capability integration and absence of the retired active Change.
