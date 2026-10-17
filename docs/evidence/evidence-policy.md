---
subject: openai-responses-proxy:evidence-policy
role: policy
state: canonical
relations:
    part_of: README.md
---

# Evidence Policy

Status: canonical.

A result supports acceptance only when its scope, verifier, exact source
revision, evidence, and limit are explicit. The repository has no tracked
`evidence/` taxonomy: source, tests, Git history, OpenSpec archives, release
artifacts, and ETHOS-selected Attestations already own the required facts.

Claims are propositions inside a bounded result, not a file family. Historical
explanation belongs in the OpenSpec archive, a decision record, the Changelog,
or Git history; Chronicle is not a separate evidence primitive.

- **Source evidence:** unit tests, compile checks, metadata checker, and CI.
- **Runtime evidence:** installed payload manifest,
  `openai-responses-proxy status --json`, verified listener identity, bounded
  runtime counters, and a bounded reload receipt when requested.
- **User-visible evidence:** a successful response in the original failing
  conversation is distinct from transport health.

Provider-portability acceptance requires the unchanged original conversation
to complete at least two turns on each leg of
`DMXAPI -> UCloud -> AIHubMix -> DMXAPI`. Before the sequence, record the exact
JSONL length and SHA-256 of that immutable prefix plus metadata for the relevant
SQLite stores and per-conversation model selection. After every leg, verify the
same prefix byte-for-byte and re-observe the metadata. New JSONL suffix bytes
are expected conversation output; rewriting any baseline byte or changing the
observed metadata fails acceptance. A proxy health check, direct endpoint smoke,
or a new conversation cannot substitute for this result.

Do not treat a green local process, a new clean conversation, or a generic log
grep as proof that an historical conversation recovered. Keep transient 429,
477, and upstream SSE failures separately classified from payload-schema fixes.

Use `openai-responses-proxy status --json` for current loopback diagnostics. Logs
are bounded secondary material and must not preserve request bodies, prompts,
credentials, headers, tokens, query strings, or raw upstream errors. Process
counters reset with the listener and prove neither an earlier conversation nor
a future request.

Historical context never substitutes for fresh acceptance bound to the exact
release commit.

## Storage and retention

Paths below are relative to the active worktree unless a tool selects them.

| Output                                              | Owner and lifetime                                                                                                                         |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Build intermediates and release candidates          | Nox session `tmp/` directories, ignored `build/` and the hosted `.release-assets/` projection; remove when superseded or published         |
| Raw verification output needed for an open decision | Ignored `build/verification/<source-commit>/`; retain only until that decision is resolved and its required evidence has a durable owner   |
| Temporary downloads, extraction, and test scratch   | One private directory per operation under `.cache/tmp/` or the tool-owned Nox session `tmp/`; reclaim on success, failure, or interruption |
| Dependency caches                                   | The package manager's cache; shared only where the tool supports it                                                                        |
| ETHOS evidence and coordination                     | The current command's native artifact reference; ETHOS owns its Git-common-dir storage and retention                                       |
| Published evidence                                  | The exact source revision's CI artifacts and release assets; local-only work retains required native evidence without depending on a Forge |

Operating-system temporary storage remains suitable when a tool needs it, but
not for long-lived handoffs. An operation owns cleanup, including after a
failed child process; after a crash, remove only its exact stopped resources.
Before retiring a worktree, preserve evidence still needed by a pending decision
at its existing authoritative owner, then remove disposable output.

Tests use pytest-owned temporary paths. The native retention policy removes
completed test runs instead of retaining hidden copies; required diagnostics
belong in the source-bound verification output above. An explicit `--basetemp`
is an operation-owned override: that operation must reclaim it. Interrupted
processes and native services still require exact ownership checks before their
files are removed; fixture-directory deletion does not prove service teardown.

The tracked ignore policy excludes generated state at these declared roots and
optional code-intelligence projections. Authored configuration remains visible
to Git; a file named `config.toml` is not inherently disposable or secret.
Credentials, publication identity and trust inputs belong outside the checkout,
at explicitly supplied operator paths. Ignore patterns do not provide secret
protection; the repository's secret scan checks admitted source.

Keep original command output when a consumer needs it. A handwritten
`receipt.json`, copied verdict, or checksum of an agent summary is not another
proof authority. Reference the tool-native result rather than duplicating it;
external observations must be refreshed when the decision depends on them.
