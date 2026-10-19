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

The public identity is **OpenAI Responses Proxy**, with command, package,
repository and release slug `openai-responses-proxy`. OpenAI names the protocol,
not affiliation or fault attribution. A public identity migration changes these
surfaces, native services and both remote repository names together at a major
release, qualified by a non-Codex client. Preserve remote IDs, history,
permissions, Issues, proposals and immutable releases; retire old active aliases.
Naming changes neither protocol scope nor Account, credential, model or client
projection ownership.

### Bounded compatibility delivery

The approved delivery increment covers the existing compatibility, independent
operation, relevant lifecycle, naming and installed-consumer obligations. It
does not make unrelated repository-wide quality, topology or dependency work a
prerequisite. The existing tasks remain the only progress authority; section 14
binds this increment to those obligations without marking broader tasks done.

Client requirements and route selection remain with their control plane;
provider wire facts and transformations remain with Proxy's protocol owners.
No new compatibility registry is introduced. Preserve indispensable encrypted
state and qualify its issuer/model boundaries rather than discard opaque content.

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
restores the client's native namespace after readable call content is proved.
Only function calls regain their empty encrypted-argument marker; custom calls
retain their native `input`. A request-local selection binds JSON, streaming
output and replay. It preserves call content, call identities and old
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
and signing remain release-construction concerns. ETHOS owns Git branch
projection and release-tag selection and publication; repository settings,
runner admission, and tag verification remain Forge concerns rather than a
second release publication mechanism.

Publication admits downloadable bytes and complete Release links, not names
alone. GitLab compares link name, URL and type, allowing only ordering and
server-assigned metadata to vary. Creation, reuse and concurrent creation all
finish with persisted Release readback after signed-byte verification. Isolated
HTTP fixtures prove adapter behavior, not hosted publication or installation.

`tools/release/artifact` owns frozen-input normalization, native packaging,
archive/manifest/checksum grammar, complete-set assembly and external OpenSSH
trust. Its `normalize`, `pack`, `assemble` and `verify` commands serve both
publishers; neither publisher reconstructs asset grammar or build internals.
Verification authenticates the signature against explicit external trust before
success. Tests follow these owners; flat entrypoints and empty facades retire.

Repository-specific quality checks share the `tools.quality.repository` package:
topology, names, and decision records feed its existing audit command. Tests
import each actual owner directly rather than reload files under synthetic module
names. The unused worktree fingerprint command and implementation are removed;
no replacement state authority or compatibility import remains.

A package survives only when it owns a distinct invariant and dependency
boundary. Catch-all packages and names such as `common`, `shared`, `utils`,
`helpers`, `misc`, `manager`, or `service` are not terminal names. The former
`service` package is now `serving`, named for the process it runs, and its
private executable roles live in `serving/roles.py`.

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

Native tools own generic analysis, formatting, testing, dependency/secret audit,
CI validation, signing, resolution and execution. Repository code remains only
for product semantics, cross-file authority, release identity, exact native
ownership or projections unsupported by admitted tools. Adoption must reduce
custom code and authorities; reject tools whose upkeep exceeds their benefit.

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

Ruff owns blocking `C901` complexity across product, tools, tests and Nox. Keep
20: the 15 probe flags ten validation, transaction and streaming owners without
proving that splitting their invariants lowers risk. ETHOS alone owns the 512
file-ELOC bound; do not duplicate it locally. Function ELOC includes decorators
and excludes only comments and actual docstrings. Syntax statements count
semicolon-separated nodes, not physical newlines. The existing audit measures
module, class and function control depth independently; same-indent `elif` is a
sibling. Architecture policy owns a five-block bound, matching Ruff's guidance.
`PLR1702` is preview-only, so retain this owner until stable rather than alter
unrelated lint semantics. Missing or invalid depth policy cannot admit source.

File-role coverage does not prove package cohesion. CLI parsing/presentation and
lifecycle observation/mutation/locking retain separate owners, with output capture
in one fixture. Test relocation preserves bodies and parameterization. Native
coverage.py includes product, repository tools, Nox and unexecuted namespace
modules; admission evaluates their declared source scopes independently, never
substituting a product aggregate for tooling or orchestration evidence.

The terminal acceptance scopes are explicit:

| Concern                    | Protected invariant                                                              | Executable owner and acceptance                                                                                              |
| -------------------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Semantics and topology     | One responsibility, state owner, and dependency direction                        | Product ontology plus architecture checks; review real caller edges and mutation ownership, not file counts                  |
| Source correctness         | Typed boundaries, explicit errors, safe concurrency and resource lifetime        | Ruff, Ty, focused behavior tests, then native platform contracts                                                             |
| Structural maintainability | A change remains locally understandable without caller-coordinated recovery      | Ruff complexity, shared file ELOC and repository control-depth admission; trustworthy syntax counts locate semantic hotspots |
| Test quality               | Assertions exercise a product invariant at the correct evidence scope            | Domain tests, failure-path contracts, and native artifact journeys; fixtures own exact cleanup                               |
| Coverage                   | Missing behavior cannot disappear in aggregation or omitted roots                | Coverage configuration declares product and tooling scopes separately; report each supported measurement honestly            |
| Dependencies and security  | No unowned dependency, exposed secret, unsafe update, or unsupported trust claim | Locked dependency, dead-code, secret, vulnerability, license, SBOM, and signature checks at their native owners              |
| Non-code carriers          | Configuration and documentation are current, valid, navigable, and readable      | Native format/lint/schema/link tools plus narrow cross-carrier product contracts                                             |
| Environment and delivery   | The released executable works outside this checkout and shell                    | Locked lane bootstrap, installed-wheel tests, native build and lifecycle acceptance, exact-source CI and assets              |
| Control effectiveness      | A declared failure reaches a nonzero decision everywhere it is required          | Isolated conformance cases run the real tool and configured command; inspect local hooks and CUE event routes                |

Reject a universal statement/function-ELOC veto for this increment, not their
measurement or semantic review. Ruff's stable `PLR0915` default of 50 reports
six product, twelve test and two tool functions after simplification; 100 was
probed, not selected. CLI checks, installed-state prerequisites, opaque-content
markers, local-tool metrics, safe paths and control fields share their existing
owners without new helpers. SSE binds commitment, deadline, retry and release;
native journeys bind one installation through upgrade, rollback and teardown.
One-use extraction would hide those invariants. Tool audits retain one complete
ownership decision; test tables retain explicit assertions and cleanup. Review
each finding by its invariant and caller edges, not today's maximum. Complexity,
depth and shared file size remain blocking. The [native rule catalog](https://docs.astral.sh/ruff/rules/)
owns tool guidance. Reassess on demonstrated net benefit; no filename exemption,
suppression or diagnostic baseline is added.

Complexity conformance runs the locked Ruff module with the actual repository
configuration against each Python role. Boundary and over-bound inputs differ
by one branch; a disabled rule, test exclusion, ignored diagnostic or successful
exit on excessive complexity fails the contract. The existing Nox static-check
owner propagates that result through quick, quality and both CUE projections.
SSE tests separate wire framing from pre-content recovery, retain their original
bodies and parameterization, and cover byte-order preservation for fragmented
input. Downstream commitment is one irreversible state; it does not need a
second prelude-flushed flag or an extra terminal flush.

Topology follows admission, generation transition, rollback/recovery, cleanup,
replay and streaming invariants. Keep each transition's validation, effect and
compensation together. Tests separate unit, integration, native and release
claims; platform names express real dispatch. Retire generic suffix clusters
through consumer-owned consolidation, not directory depth or forwarding aliases.
Acceptance proves preserved behavior and fewer parallel owners.

Quality repair follows the existing task dependency order:

1. Repair measurement/scope errors and unsupported completion claims; preserve
   the installed service.
2. Consolidate rule ownership and prove the actual invocation and failure exit.
   Record justified bounds; leave unproved scopes open.
3. Close product, test and fixture owners together; delete displaced paths and
   imports before advancing. Apply the same rule to tools and non-code carriers.
4. Enable the agreed policies without blanket exclusions or permanent baselines;
   focused checks precede expensive proof.
5. Freeze an exact candidate for CI events, cold bootstrap, native lifecycle,
   security and performance acceptance. Release identity stays immutable while
   later delivery evidence completes tasks, archive and retirement.

Generic ETHOS scope, lifecycle, hook dispatch, and capability-reporting defects
belong to ETHOS. Product policy, native tool configuration, tests, and CI remain
this repository's responsibility and continue without waiting for ETHOS changes.

Coverage.py owns discovery and executable counts; the existing admission command
owns nonzero decisions for independent product, tooling and orchestration scopes.
Keep their floors and resolve gaps with meaningful tests, deletion or simpler
responsibilities, not combined denominators or product-only green evidence.

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

Nox owns target environments and installer selection. Repository code exports
locked dependencies rather than treating `session.python` as an environment
path. Installed-product admission requires imports and manifests beneath that
interpreter's installed package directory; an editable `.pth` into the checkout
cannot qualify merely because execution uses another working directory.

Orchestration tests observe real Nox command ordering, environment ownership and
failure exits. Artifact tests run isolated installed and source-linked fixtures.
These distinct owners under `tests/quality/orchestration` share native pytest
fixtures, replace source-string assertions and do not claim native operation.

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

Recovery of an interrupted first installation stops candidate supervision and
proves owned-process exit before discarding its payload. A missing health
response cannot distinguish an absent service from a registered service still
starting. The transaction admits the exact selector, rollback snapshot, and
command restoration first, then hands its verified candidate context to the
native teardown operation shared with installation compensation. That operation
removes only a service whose configured executable is the candidate's; a missing
configured identity is usable only with independently proved native absence.
Mismatched or unproved supervision preserves recovery authority. Windows queries
the exact local task with `schtasks /hresult`: only
`HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)` and
`HRESULT_FROM_WIN32(ERROR_PATH_NOT_FOUND)` mean absent, while other errors,
malformed XML, and timeouts preserve unknown state.

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

Windows can reject a delete while a native image or external handle is still
mapped after the process-exit barrier, and a later empty holder snapshot does
not prove what held the file. The same purge owner therefore allows only native
delete-access and sharing errors one shared five-second retry window. Each
attempt revalidates the captured file and ancestor identities, no-follow
regular-file access, and the journal digest; a replacement stops disposal even
with equal bytes. No permission change, holder termination, background deletion,
or error suppression is added, and persistent failure keeps the native cause and
recovery authority.

Process termination preserves its exact failure phase and a safe native reason.
Capture denial, generation mismatch, unreadable status, signal failure, wait
timeout, and unreadable timeout observation remain distinct unproved outcomes,
and none of them establishes that a process survived. Deployment preserves
recovery authority after an unknown predecessor exit instead of rolling back
or disposing the payload on a collapsed boolean. Existing success, proven
disappearance, zombie handling, identity refusal, and deadlines are unchanged.
Test teardown records the first safe failure while continuing independent,
already-owned processes, and an unproved exit prevents the corresponding
payload and outer native test root from being discarded.

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

Lifecycle convergence selects one operation from verified installed state.
Except transaction recovery, it requires a verified selected payload, installed
record and command, no pending transaction, and the requested installed port.

| Admitted state                                                                   | Command         | Required result                                                    |
| -------------------------------------------------------------------------------- | --------------- | ------------------------------------------------------------------ |
| No listener; owned stopped supervisor; port free                                 | `reload`        | Bind supervision, prove selected listener, return `started`.       |
| Verified live runtime; owned stopped/outdated supervisor                         | `reload`        | Prove handoff, then bind installed adapter rendering.              |
| Same signed artifact; absent/stopped supervision; matching listener or free port | `install`       | Restore command/supervision, prove serving, retain rollback state. |
| Answering verified successor and admitted retained predecessor                   | `rollback`      | Create the reverse transaction; stopped state needs reload first.  |
| Interrupted admitted transaction                                                 | `recover`       | Complete only recorded restoration/disposal, never start intent.   |
| Unobservable or mismatched supervision                                           | `status --json` | Read-only diagnosis, no inferred rebind.                           |

A failed start proof leaves its declaration bound because no predecessor serves.
A rebind failure after proved handoff names the successor and reverses nothing.

The three adapters share these preconditions and operator deadlines. Reload
rebinds after every proved handoff, not merely after a payload change. Absent
supervision requires explicit installation intent; `doctor` selects
`install --help`, never reload. Same-artifact reconciliation refuses running or
unobservable supervision, another artifact, unverified payload/command, another
port or a foreign listener. It does not erase retained state through purge.

`status` observes native projection through the adapter. An owned declaration
bound to the control executable but differing from installed rendering is
`outdated`; `doctor` selects reload. macOS/Linux compare carrier bytes. Windows
compares the task action because Task Scheduler normalizes exported task XML,
so other Windows definition drift remains outside that observation. A mismatched
executable is `degraded`, not restart authority. launchd enforces label exclusion
between user and GUI domains; binding replaces an owned registration in the
unselected domain rather than treating both domains as independent services.

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

Runner admission binds the actual execution OS and architecture to the CUE
target. A macOS ARM64 Docker host does not prove Linux x86_64 or Windows.
Historical CI runs remain Forge evidence, not standing proof of current source.

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

Live validation owns fixed structural reason types consumed by the relay;
arbitrary exceptions remain `unknown`, never logged exception prose. A historical
ValueError does not identify its failing validator or prove a decryption relapse.
Committed-response acceptance covers malformed events, rejected ciphertext and
unknown failures while preserving emitted bytes, closure, privacy and no reopen.
It changes neither admission nor retries nor client state. Source/native proof
is distinct from release and installed acceptance; the current serving generation
remains untouched until the successor passes its declared transition.

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
