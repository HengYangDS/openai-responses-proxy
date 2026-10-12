## Context

See [proposal.md](proposal.md). This Change is the sole active repository-wide
convergence carrier. Its tasks are the progress authority; no parallel roadmap,
quality backlog, compatibility matrix, or cleanup ledger will be added.
Implementation may use multiple signed atomic commits, but all remain in this
one Work Lane and close requirements from this one Change.

The accepted product is a local Responses data plane. It receives one supported
Responses request, validates and projects it to one configured upstream wire
contract, returns one faithful Responses result, and owns only its installed
payload, local listener, native supervisor, transaction state, and bounded
operational output. It does not own client configuration, model choice,
credentials, conversations, repository governance, or Forge identity.

## Product Positioning and Name

The terminal data-plane contract is OpenAI Responses, not every API commonly
called "OpenAI-compatible". Provider admission is declarative and independent of
provider brand; a Chat Completions-only endpoint does not qualify. Codex is the
first qualified client, but generic request admission must not require Codex
identity. Codex-specific historical replay belongs to a narrow compatibility
policy rather than the common Responses grammar. Other clients require their
own real request, replay, stream, error, and installed-client acceptance before
the product claims support.

The operator-approved target public name is **OpenAI Responses Proxy**, with
the product, command, package, repository and release slug
`openai-responses-proxy`. OpenAI identifies the Responses protocol; it does not
claim upstream affiliation or assign every failure to the official service.
Change the package, command, service, release identity, local repository family
and both existing remote repository names together at a major release only
after a non-Codex Responses client passes that bar. Preserve remote repository
IDs, history, permissions, Issues, proposals and published releases. Remove the
old active identity in the accepted transition rather than maintain aliases.
Until then, retain the truthful shipped name **OpenAI Responses Proxy**. Naming
does not expand the protocol surface or move Account, credential, model, or
client-projection authority out of AIGW or another control plane.

### Bounded compatibility delivery

The approved delivery increment covers the existing compatibility, independent
operation, relevant lifecycle, naming and installed-consumer obligations. It
does not make unrelated repository-wide quality, topology or dependency work a
prerequisite. The existing tasks remain the only progress authority; section 14
binds this increment to those obligations without marking broader tasks done.

First reconcile the AIGW maintenance obligations and recorded upstream failures
with their actual evidence. Then refine the existing route/client compatibility
intersection under AIGW and the protocol decisions under Proxy in parallel.
Client requirements, provider wire facts and Proxy transformations retain their
owning implementations; no new control plane or compatibility registry is
introduced. Preserve indispensable encrypted state, and qualify its issuer and
model boundaries rather than treat opaque content as disposable corruption.

Qualify Codex on its selected UCloud route, installed Hermes on its selected
DMXAPI route, and an ordinary installed OpenAI-SDK Responses client. Their real
journeys cover tool execution and output replay, continuation, terminal stream
integrity, structured errors and native compaction where supported. Prove the
Proxy also operates with an ordinary explicit upstream without AIGW, client or
Forge installation dependencies. Source tests and runtime counters cannot
replace those consumer results.

Only then release the complete name migration through the native owners. Bind
the same signed stable source and selected-platform assets to both Forges,
verify local and remote repository identities, native installation, upgrade,
rollback and restored serving, and migrate every active reference. Preserve
credentials, user models, sessions, historical evidence and foreign work.
Perform exact owned cleanup after each bounded operation. Two undecodable
historical ciphertext messages remain preserved and outside plaintext recovery
acceptance; universal client/provider immunity is outside this increment.

## Goals / Non-Goals

**Goals:**

- Reconstruct the repository from product semantics rather than preserve its
  current file tree.
- Assign every retained source, test, tool, configuration, document, workflow,
  and artifact to one semantic owner and one dependency direction.
- Delete duplicate owners, obsolete carriers, compatibility paths, historical
  residue, and custom mechanisms superseded by mature tools.
- Prove supported behavior through clean-room development, installed wheels,
  native artifacts, real operating systems, installed lifecycle transitions,
  and both optional Forge publication planes.
- Keep the current working installation available until an exact accepted
  successor asset has passed the corresponding transition proof.

**Non-Goals:**

- Add AIGW, Codex session, client-profile, credential-manager, or model-selection
  responsibilities to this repository.
- Copy ETHOS lifecycle, Lease, Commitment, Attestation, or branch-transition
  semantics into product code.
- Retain an old path solely for compatibility, preserve an entity because it
  exists, or add a second framework when the existing authority can carry the
  requirement.
- Treat a container, mock, syntax check, or another operating system as native
  evidence for an unavailable platform.

## Decisions

### Select readable delegation before generation

One released provider profile selects native or plaintext agent delivery; the
protocol package owns the complete request/response adaptation. The plaintext
selection aliases the reserved collaboration namespace and its explicit tool
choice, removes only message encryption annotations before generation, and
restores the client's native namespace and empty encrypted-argument marker
after readable arguments are proved. A request-local selection binds JSON,
streaming output, and replay. It preserves arguments, call identities and old
ciphertext; failures do not retry with missing messages. Client configuration
remains with its existing owner and must align the namespace and model agent
version. Native compaction's empty current-turn tool catalog is a valid control.

Acceptance uses the signed client and selected route for actual child work,
bidirectional messages, automatic completion, followup tasks, native resume,
compaction and exact content witnesses. Source acceptance precedes release,
native installation, scoped client selection, original-chat acceptance and
rollback. Old ciphertext readability is a separate unproved boundary until a
supported decode or exact source binding establishes it.

### One Change, ordered semantic closures

The Change is large by design, but implementation is not a big-bang edit. Each
ordered task group closes one semantic boundary through RED, one owner, deletion
of the incumbent path, focused GREEN, and one affected gate. Heavy gates run
only at milestone boundaries. A task is complete only when its stated evidence
exists; file churn or an unexecuted design does not count as progress.

### Product ontology determines physical topology

The durable product domains are request admission, portable Responses
semantics, Provider wire adaptation, relay transport, runtime configuration,
installed payload generations, lifecycle transactions, native supervision, and
public CLI presentation. Repository-only domains are development bootstrap,
quality, CI projection, release construction, Forge transport, and publication
verification. Tests mirror these owners by behavior. Provider differences live
within the owning semantic package, rather than in suffix-named sibling files.

The protocol package separates request-local replay from failure recovery and
live response validation. `replay` owns item relationships, content projection,
and whole-request projection. `recovery` consumes those rules to handle input
validation and execution failures; it does not own another replay grammar.
`response` validates live wire data without rewriting it. Tests mirror replay
projection, content, history, admission, and recovery instead of suffix families;
shared request encoding belongs to their fixture, not another test module.

The release publication package owns one command tree and one subpackage per
Forge. Within each Forge, publication writes and hosted observations are distinct
operations. Cross-Forge verification consumes observations; artifact assembly
and signing remain release-construction concerns. ETHOS publication owns Git
branch projection; repository settings and runner admission remain Forge
concerns rather than a second release publication mechanism.

Publication identity includes what users can download, not merely a matching
asset-name inventory. The GitLab adapter compares the complete expected link
records (name, URL, type), allowing only response ordering and server-assigned
metadata to vary. Every success path ends by reading and validating the
persisted Release after verifying package bytes, including first creation,
existing Release reuse, and concurrent creation. Signed package verification
and Release-link verification protect different boundaries; neither replaces
the other. An isolated HTTP store proves these adapter contracts without
claiming real Forge publication or changing an installed product.

Release construction has one semantic owner under `tools/release/artifact`.
`bundle` normalizes frozen inputs and packages one native target; `format` owns
archive, manifest, and checksum grammar; `assembly` admits one complete release
set; `signing` owns the external OpenSSH trust boundary. One command tree exposes
`normalize`, `pack`, `assemble`, and `verify`. Publication adapters consume these
owners without creating another asset grammar or coordinating build internals.
The `verify` operation authenticates the signature against explicit external
trust before reporting success; signature-file presence is not authentication.
Both Forge publishers consume that same complete verification boundary.
Tests follow bundle, format, assembly, and signing semantics. The former flat
asset entrypoints and declaration-only publication initializer are removed.

Repository-specific quality checks share the `tools.quality.repository` package:
topology, names, and decision records feed its existing audit command. Tests
import each actual owner directly rather than reload files under synthetic module
names. The unused worktree fingerprint command and implementation are removed;
no replacement state authority or compatibility import remains.

A package survives only when it owns a distinct invariant and dependency
boundary. Catch-all packages and names such as `common`, `shared`, `utils`,
`helpers`, `misc`, `manager`, or `service` are not terminal names. Existing
`service` usage is decomposed by actual runtime, supervision, handoff, and
entrypoint ownership instead of being preserved as a generic bucket.

Lifecycle control tests follow status, recovery, reload, rollback and uninstall
semantics; loopback transport belongs to runtime tests. Their shared installed
listener evidence stays in the existing lifecycle fixture, not an umbrella test
class or another fixture framework. Pytest owns temporary test directories and
retires them through its native retention policy; tests do not allocate unowned
`mkdtemp` roots. Explicit per-operation output roots retain their own cleanup
owner, and required failure evidence is copied to source-bound verification
output before scratch retirement.

### Positive topology is the architecture authority

One declarative topology covers product source, repository tools, tests,
configuration, specifications, documentation, workflows, generated projections,
and root files. It declares owners, allowed edges, public entrypoints, generated
outputs, and retirement conditions. New undeclared entities fail because they
lack a positive owner, not because their names appear on a growing forbidden
list. Exceptions are typed, justified, expiring, and consumer-bound; free-text
baselines and permanent suppressions are not admission.

### Mature tools own generic mechanics

Ruff, Ty, pytest and focused plugins, coverage.py, Bandit or another selected
Python security analyzer, deptry, markdownlint, a Markdown formatter, lychee,
Taplo, actionlint, CUE, gitleaks, pip-audit or OSV-compatible scanning, Syft,
Git, OpenSSH, uv, mise, and Nox are evaluated against their supported scope.
Custom repository code remains only for product semantics, cross-file authority,
release identity, exact native ownership, or CI projection that an upstream tool
cannot express. Adoption requires net reduction in custom code and authorities;
a tool is rejected when configuration and maintenance exceed the mechanism it
would replace.

### Quality acceptance protects product invariants, not a green dashboard

The quality system has four distinct obligations: a useful policy, a correct
measurement, a complete execution path, and a working product. Passing one does
not establish the others. The responsibility map assigns concerns; native tool
configuration defines their executable rules; Nox invokes them; CUE projects the
same obligations into each Forge. No second checker, registry, or workflow may
independently define the same rule.

Quality inventory binds Git's directory and working tree to the requested
checkout, rather than treating the command's current directory as repository
identity. Native Git resolves both regular `.git` directories and linked-lane
`.git` files. A nested non-checkout must fail instead of inheriting its parent's
index and producing a plausible but unauthorized scope. Structure, names, text,
responsibility mapping, and native formatter inputs use this same boundary;
each retains its own explicitly declared tracked or pending-file semantics.

The review at source `268ce99f` established these gaps:

- Structural inventory still reports size observations without ELOC vetoes;
  function size, arguments and statement admission remain open. McCabe complexity
  now has one blocking Ruff `C901` policy across product, tools, tests and Nox.
  The stream reader drops duplicated prelude state and two closure layers;
  declaration identity validation has one owner instead of three copies.
  Their complexity decreases from 24 to 20 and from 22 to 16 respectively.
  The current bound of 20 is a reviewed maintainability guard, not a claim that
  every smaller value is better. A bound of 15 identifies nine additional
  operations, including payload validation and transaction-state admission;
  review their semantic risk before fragmenting their state or adding facades.
  The numeric authority remains Ruff configuration, not this explanation.
- Function ELOC used a physical line span, while file ELOC removed entire lines
  carrying inline comments. The two measures were neither accurate nor equal.
  Commit `6110515b` repairs that measurement with focused regression evidence;
  this does not complete structural enforcement or the quality system.
- Top-level package admission and a complete file-role inventory did not prove
  cohesion within a package. Lifecycle control and CLI contracts now follow
  their operation boundaries rather than stateless umbrella classes. CLI parsing
  and presentation remain distinct from lifecycle observation, mutation and
  locking; one CLI fixture owns output capture. Relocation preserves test bodies
  and parameterization, rather than manufacturing additional tests or introducing
  a fixture framework. Lifecycle transaction tests, relay fixtures and native
  compatibility journeys remain concrete review targets.
- The former Python coverage configuration omitted repository tools and Nox
  orchestration. Real coverage.py conformance now includes those sources and
  unexecuted namespace modules. The admission command consumes that same native
  configuration and evaluates each source independently; a product aggregate
  cannot establish tool or orchestration coverage. This exposes existing test
  gaps rather than completing them.

The terminal acceptance scopes are explicit:

| Concern                    | Protected invariant                                                              | Executable owner and acceptance                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Semantics and topology     | One responsibility, state owner, and dependency direction                        | Product ontology plus architecture checks; review real caller edges and mutation ownership, not file counts                   |
| Source correctness         | Typed boundaries, explicit errors, safe concurrency and resource lifetime        | Ruff, Ty, focused behavior tests, then native platform contracts                                                              |
| Structural maintainability | A change remains locally understandable without caller-coordinated recovery      | Native complexity, branches, statements, arguments, and nesting checks; trustworthy ELOC locates module and function hotspots |
| Test quality               | Assertions exercise a product invariant at the correct evidence scope            | Domain tests, failure-path contracts, and native artifact journeys; fixtures own exact cleanup                                |
| Coverage                   | Missing behavior cannot disappear in aggregation or omitted roots                | Coverage configuration declares product and tooling scopes separately; report each supported measurement honestly             |
| Dependencies and security  | No unowned dependency, exposed secret, unsafe update, or unsupported trust claim | Locked dependency, dead-code, secret, vulnerability, license, SBOM, and signature checks at their native owners               |
| Non-code carriers          | Configuration and documentation are current, valid, navigable, and readable      | Native format/lint/schema/link tools plus narrow cross-carrier product contracts                                              |
| Environment and delivery   | The released executable works outside this checkout and shell                    | Locked lane bootstrap, installed-wheel tests, native build and lifecycle acceptance, exact-source CI and assets               |
| Control effectiveness      | A declared failure reaches a nonzero decision everywhere it is required          | Isolated conformance cases run the real tool and configured command; inspect local hooks and CUE event routes                 |

Structural limits are engineering decisions, not universal constants. First
measure with named semantics; then assess the protected risk, current
percentiles, worst legitimate cohesive examples, and native tool guidance.
Reject both a bound chosen just above the current maximum and a number that
forces forwarding helpers or state fragmentation. Product and tooling branches
are executable risk; a long declarative test table is not equivalent, but test
setup, control flow, assertions, and teardown still need bounded ownership.
Any distinct scope needs a semantic reason, never a filename exception for a
current offender. Existing gaps remain open until the chosen final policy
passes; an informational probe is never called a blocking gate.

Ruff remains the first owner for its native complexity rules, documented in the
[rule catalog](https://docs.astral.sh/ruff/rules/). ELOC and logical statements
must be named separately. Module size is reviewed with cohesion and dependency
edges; moving half a function into an alias package does not satisfy either.
Do not add another analyzer until its required metric or behavior is absent
from the admitted tools and its maintenance cost is lower than the displaced
custom implementation.

Complexity conformance runs the locked Ruff module with the actual repository
configuration against each Python role. Boundary and over-bound inputs differ
by one branch; a disabled rule, test exclusion, ignored diagnostic or successful
exit on excessive complexity fails the contract. The existing Nox static-check
owner propagates that result through quick, quality and both CUE projections.
SSE tests separate wire framing from pre-content recovery, retain their original
bodies and parameterization, and cover byte-order preservation for fragmented
input. Downstream commitment is one irreversible state; it does not need a
second prelude-flushed flag or an extra terminal flush.

Topology changes follow complete semantic operations. Review payload admission,
generation transition, rollback/recovery, cleanup, portable replay, and streaming
as distinct invariants. Keep each state transition's validation, effect, and
compensation together. Tests follow those behaviors and separate unit, integration,
native, and release evidence without copying product implementation structure.
Platform filenames may remain when they express real platform dispatch; generic
suffix clusters and fixture buckets must instead be absorbed, narrowed, or
split around real consumers. The acceptance proof is preserved behavior, fewer
parallel owners, and understandable dependencies, not directory depth.

Quality repair proceeds in this order within the existing task groups:

1. Correct measurement and scope errors, record current effective tool settings,
   and reopen unsupported completion claims. Keep the installed service intact.
2. Consolidate native rule ownership and test the complete invocation/exit path.
   Document selected bounds before changing them; keep unproved scopes open.
3. Resolve product hotspots by invariant, then organize their behavioral tests
   and fixtures in the same closure. Delete displaced code and imports before
   moving to the next owner. Review tooling and non-code carriers by the same rule.
4. Enable the agreed blocking policies with no blanket exclusions or permanent
   violation baseline. Run focused checks after each closure; batch independent
   formatter fixes before running expensive proof.
5. Exercise all CI event classes, cold bootstrap, native artifacts and lifecycle,
   security and performance, then freeze one exact candidate for full acceptance.
   Exact applicable proof and current source authority permit a releasable increment.
   Delivery and actual-use obligations consume that release; whole-Change archive
   and lane retirement follow their observed completion. Preserve immutable release
   identity when later task evidence updates the active Change.

Generic ETHOS scope, lifecycle, hook dispatch, and capability-reporting defects
belong to ETHOS. Product policy, native tool configuration, tests, and CI remain
this repository's responsibility and continue without waiting for ETHOS changes.

The coverage repair is deliberately limited to that owning boundary: no new
scanner, waiver baseline, product mutation, or quality configuration is added.
Coverage.py owns file discovery and executable counts; the existing admission
command owns the independent source-root decision and its nonzero exit. Product,
tooling, and orchestration each retain the current floor. Their measured gaps
must be resolved through meaningful tests, deletion, or simpler responsibilities,
not by combining denominators or silently reusing a product-only green proof.

### One development and supply-chain control plane

`mise` selects locked cross-platform tools and exposes the small developer task
graph. uv owns Python resolution and per-Work-Lane environments. npm owns only
the locked Node tools still required by OpenSpec or documentation. Nox owns the
Python verification matrix. Each Work Lane gets independent mutable `.venv`,
`.nox`, `node_modules`, build, coverage, and test-temporary state; only
content-addressed caches are shared. Bootstrap is an idempotent reconstruction
from locks, never an ambient-system repair.

Direct runtime, development, action, image, and release-tool versions are
verified online at the time of the supply-chain task, advanced to current stable
releases, locked, and tested. No version is considered current merely because a
prior audit said so. Pixi, Nix, Bazel, Just, Task, shell wrappers, and another
update bot are not added unless a proved requirement cannot be carried by this
control plane.

Nox's native session installation API owns the target environment and selected
installer. Repository code exports the locked dependency set and requests its
installation; it does not reinterpret `session.python`, which is a version
selector rather than an environment path. The installed-product probe requires
the imported package to resolve beneath that interpreter's installed-package
directory and its manifest beneath the same product package. Merely resolving
outside a temporary working directory does not prove source independence:
an editable `.pth` projection can still point back into the checkout.

Orchestration contracts execute the declared sessions through Nox and observe
their command boundary, environment ownership, ordering, and failure exits.
Artifact admission additionally runs the actual isolated interpreter against
installed and source-linked fixtures. These contracts replace source-string
assertions; they prove orchestration, not native product operation. Artifact
admission and session execution have separate test owners under
`tests/quality/orchestration`, with domain-local fixtures in pytest's native
`conftest.py` carrier.

### One native process environment contract

`src/openai_responses_proxy/runtime/process_environment.py` owns derivation of
native child-process environments for product runtime and black-box acceptance. It preserves
the supported host execution substrate, removes inherited Proxy and Python
injection state, redirects all product-owned roots to test-owned locations, and
accepts an explicit empty product `PATH`. It binds declared user, payload, and
state roots before the child changes working directory. Product processes,
fixtures, and packaged CLI contracts consume it directly. Nox delegates native
acceptance to those contracts instead of implementing a second command runner.
Partial environments, Windows `SystemRoot` exceptions, and platform environment
allow-lists are deleted.

### One lifecycle state machine, three native adapters

The lifecycle core owns prepare, verify, commit, observe, rollback, recovery,
and retirement. macOS launchd, Linux systemd user services, and Windows
Task Scheduler adapters translate only native service operations. The Windows
projection belongs to the current user, not a machine-wide Service Control
Manager service or administrator-owned installation. Creation and teardown
consume the same exact service target, paths, executable identity, process
generation, and transaction. Successful, failed, timed-out, and interrupted
tests prove no net native-resource growth and preserve unrelated canonical
installations.

The transaction journal is one sibling file outside the disposable transaction
directory. After projection and required supervisor binding finish, the
transaction persists its terminal outcome before removing any candidate,
obsolete generation, or rollback snapshot. Recovery of that outcome performs
only disposal and deletes the journal last; it never replays terminal effects
or requires an identity already discarded during cleanup. Interrupted cleanup
continues to block a new transaction. Unrecognized journals and unowned roots
remain protected, not inferred into a cleanup permission.

Controller rollback, interrupted materialization, and serving-generation
recovery share one prior-projection restoration operation. It admits only the
transaction's exact before or after selection; serving-generation recovery also
verifies the prior runtime identity before mutation. Selector restoration and
command restoration are independent durable effects: a restored selector never
proves that the command was restored. Retry completes the missing command before
binding supervision, while an already restored command remains untouched.
Retaining the rollback target changes disposal ownership, not the restoration
algorithm; selection drift remains unowned state in every entrypoint.

Purge uses that same journal after proving native-service absence and owned
process exit. Its terminal record contains the verified, root-relative file
digests. Selection is detached first; disposal checks remaining bytes against
the recorded digests and removes only declared empty parent directories.
Neither a missing payload nor missing metadata invalidates completed removal.
Unknown content or a changed replacement keeps the journal open for deliberate
resolution. Public recovery and repeated purge consume this one disposal owner;
there is no separate uninstall log, retry engine, or inferred recursive deletion.

Fresh-install failure retains its initial boundary when native cleanup or
payload rollback also fails. Public causes stay readable; other exceptions
become bounded operation labels, never paths, types, or native output.
Unavailable service admission still triggers only payload rollback, never native
cleanup, and unconfirmed runtime cleanup preserves the recovery journal.
Generation removal reports the operating system's numeric error without its
absolute path. A failed recovery-record write is reported as unconfirmed, never
as a retained transaction. The outer install entrypoint keeps the original
public code and next command when prepared cleanup fails. Both layers use the
shared error owner; there is no second cause formatter.

Public outcome admission validates the operation, not only its JSON shape. A
completed reload requires distinct positive predecessor and successor process
IDs. Cleanup reports a nonnegative integer process count; zero remains valid
when there was no live listener to stop. The existing outcome owner rejects
inconsistent evidence before either human or machine rendering, without a
second result schema or a change to valid lifecycle output.

A capability-qualified handoff transfers the listener without changing request
admission: the predecessor stops accepting only when the successor is ready to
serve, while already accepted handlers finish on the predecessor. Upgrade and
rollback share this transition. The lifecycle uses draining only for the
bounded legacy native-generation replacement path; handoff state is never
presented as admission state.

Environment variables remain the portable non-interactive Provider-secret
input. Secret storage and client projection are outside this product. The Proxy
never opens a keyring, changes Codex or Claude configuration, or requires AIGW.

### One CI model, two optional Forge projections

CUE owns stages, jobs, dependencies, triggers, matrices, platform claims,
artifacts, cache identities, and release admission. GitHub Actions and GitLab CI
are generated projections and are checked for drift. Review SHA, proposal update,
maintainer fast-forward, `dev` promotion, `main` promotion, and tag pipelines
have explicit coverage. Equivalent exact-SHA evidence may be reused through a
revision-bound attestation; repeated jobs that prove no additional fact are
removed.

GitHub branch admission is a generated branch-only workflow that invokes the
shared Verify graph once. Its stable `Admission` job fails unless the reusable
run succeeds; the graph itself rejects failed or skipped jobs required by the
exact branch event. Tag, release, and dispatch runs cannot emit that required
branch check. Enable branch protection only after a hosted review proves the
check identity, failure behavior, and signed fast-forward path on the exact
candidate. The branch caller passes the published-predecessor trust anchor
explicitly into Verify; it never forwards the release signing key. GitLab keeps
its native successful-pipeline admission rather than a copy of GitHub's check
mechanism.

GitHub and GitLab are optional peer publication planes. Local source remains
fully buildable and installable without either. The same signed local commit and
tag object are pushed unchanged; each Forge supplies independent authentication,
CI, Release records, and asset transport. A missing GitLab Windows runner does
not erase Windows evidence already proved by the GitHub native runner, but the
GitLab projection must state that it does not provide that platform proof.

At source `93aecba5`, GitHub Verify run `35885350727` executed its Windows
Server 2025 native asset job (45 passed, one skip), Python 3.12–3.14 jobs, and
published-predecessor compatibility. GitLab MR pipeline `8003` completed six
Linux ARM64 jobs on project runner `35`. Its active tag and project variable
match CUE's `linux-arm64` target; the macOS ARM64 Docker host is not presented
as a Linux x86_64 or Windows runner. No unavailable runner gates this review,
and GitLab does not claim Windows proof it did not execute.

### Evidence and documentation are reader paths, not residue stores

Current acceptance is reconstructed from exact Git state, test and quality
results, OpenSpec, ETHOS-selected Attestations, signed release assets, and
observed runtime state. A tracked `evidence/`, Claim, Chronicle, parity tree, or
records directory survives only with a current unique consumer and retention
contract. Historical explanation belongs in immutable OpenSpec archives,
Decision Records, Changelog, release records, or Git history.

Documentation follows reader intent: product overview, concepts, setup and
operations, architecture, contribution, governance, and decisions. Every
content filename names its subject; every directory index has a navigation job.
Decision Records use `dr-<sequence>-<subject>.md`. Examples are executable and
production-shaped, never toy placeholders. Prose, tables, commands, links, and
configuration are formatted and verified.

### SemVer and destructive retirement

`VERSION` is the sole release identity. Public incompatibility determines the
next SemVer value; internal restructuring alone does not force a major release.
The existing release-metadata owner binds `VERSION`, local product tags, and a
Keep a Changelog 1.1.0 document. It permits one current pending release during
release preparation, rejects every historical untagged heading, and requires
canonical change categories. Unpublished historical headings are folded into
the later tagged release that actually carried their changes, preserving the
user-visible account without preserving fictional releases. Published releases
are immutable.
Merged proposal branches, failed unpublished intermediates, retired Work Lanes,
old hooks, orphaned runtimes, temporary services, caches, and generated residue
are removed once exact ownership and lack of consumers are proved. Deletion is a
first-class task, not deferred housekeeping.

## Risks / Trade-offs

### Bounded live-validation diagnosis

Request40032 exposed an evidence gap in the existing SSE boundary: response
parsing and selected plaintext restoration both raised ValueError, so the
operational outcome retained only an exception class. The retained log does not
prove which validation failed or whether the original decryption incident
returned. Live response validation now owns a fixed reason type consumed by the
relay; arbitrary exceptions remain unknown and no exception prose is logged.
The committed-response regression covers malformed events, rejected ciphertext
and unknown validation failures while preserving emitted bytes, exact connection
closure, no reopen and privacy. This repairs diagnostic fidelity without
changing admission, readable-message requirements, retry policy or client state.
Focused predecessor RED and successor GREEN, affected protocol/relay acceptance
and current-source native proof remain separate from published/installed
acceptance. The existing5.0.1 service stays the accepted serving generation
until this increment completes its declared release and native transition.

- **The broad Change becomes an excuse for an unreviewable patch** → keep one
  Change but use ordered atomic commits, focused proofs, and task-level
  acceptance; never combine unrelated mutations in one commit.
- **Topology work destabilizes the working service** → preserve the accepted
  installed release until a signed candidate passes native lifecycle proof;
  source restructuring does not mutate the active service.
- **Strict quality expansion creates arbitrary vetoes** → every rule records
  risk, scope, measurement, false-positive cost, remediation, and review
  condition; unsupported or redundant rules are rejected rather than enabled
  performatively.
- **Latest dependencies introduce regressions** → update one authority at a
  time, regenerate locks, run clean-room and affected-platform proof, and keep
  published assets immutable.
- **Dual-Forge automation produces different Git objects** → create and sign
  commits and tags locally once; Forges only receive exact objects and publish
  independent projections.
- **Destructive cleanup removes user or foreign state** → delete only exact
  product-owned, lease-owned, manifest-owned, branch-owned, or unreferenced
  entities after a read-only inventory and consumer proof.

## Migration Plan

1. Establish the full Change, capability deltas, task map, and exact current
   baseline; keep the installed product untouched.
2. Close the Windows native-environment P0 and prove the exact candidate on
   macOS, Linux, and Windows before any release mutation.
3. Freeze product ontology and dependency directions, then migrate one domain
   at a time while deleting each superseded owner in the same atomic commit.
4. Converge the CLI, Responses path, native lifecycle, development environment,
   quality system, CI model, supply chain, documentation, and release system in
   dependency order.
5. Run the complete local and native verification ladder once on the frozen
   candidate; repair failures at their smallest owner rather than rerunning the
   whole graph for discovery.
6. Land through current ETHOS authority, project the same signed objects to both
   optional Forges, publish immutable assets, and prove installed upgrade,
   no-op, rollback, recovery, re-upgrade, uninstall, and reinstall.
7. Remove proposal branches, the Work Lane, superseded local and remote
   artifacts, native test resources, caches, and consumerless records; then
   archive this Change and verify the repository family is terminal.
