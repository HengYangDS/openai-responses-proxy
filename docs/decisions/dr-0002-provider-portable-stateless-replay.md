---
subject: openai-responses-proxy:decision-0002-provider-portable-stateless-replay
role: decision
decision_id: DR-0002
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0002: Keep Responses Replay Stateless and Provider-Portable

- Date: 2026-08-07
- Last amended: 2026-10-08

## Context

Third-party Responses providers do not share stored conversations, response or
item identifiers, encrypted reasoning state, or replay extensions. Reusing one
provider's continuation state after a route switch causes invalid requests and
can bind a client conversation to one upstream.

## Decision

Every outbound Responses request sets `store=false` and is rebuilt from one
closed provider-portable grammar. The projection removes provider-issued
response, conversation, cache, stored-item, search and replayed-reasoning
bindings while retaining portable dialogue and complete tool relationships.
Required encrypted agent messages are native control data, not removable
provider history: preserve their original envelope and ciphertext for the
selected upstream. An upstream rejection cannot prove that the visible routing
header contains the task, so it is relayed without a ciphertext-free retry.
Unknown or structurally unproved replay material fails locally.
Current-turn `additional_tools` declarations and payload-free compaction
triggers are controls, not disposable replay history. Projection and shrinking
recovery retain them; a recovery that cannot retain them declines rather than
silently changing tool availability or compaction intent.

Recovery consumes only the already-projected representation or a strictly
smaller derivation of it. No recovery path restores an earlier provider-bound
request, reads client conversation storage, or claims to decrypt ciphertext.
Requests carrying encrypted agent messages cannot use shrinking recovery.

A released provider profile may select plaintext agent delivery before model
generation. Its request-local protocol adapter aliases `collaboration` to
`collaboration_plaintext`, removes only the three message tools' encrypted
message annotation, and remaps replayed calls and explicit tool selection. It
restores native call namespaces only for proved readable message arguments,
using `arguments` for function calls and `input` for custom calls. Only function
calls receive an empty `encrypted_function_args` list. This invokes the client's existing
plaintext delivery path; it does not decrypt or relabel old ciphertext. The
request-local selection binds both JSON and streaming response restoration.

The client configuration owner must align its tool namespace and model agent
version with the conversation. The proxy neither reads nor repairs these
settings. Native compaction's empty `additional_tools` list is a valid explicit
control and must remain empty rather than be removed or rejected.

## Alternatives Rejected

- **Reuse provider-stored continuation state:** rejected because opaque response,
  item and reasoning bindings have no shared cross-provider meaning.
- **Drop encrypted delegation or retry only its routing header:** rejected
  because an accepted envelope does not prove delivery of the required task.
- **Guess the meaning of unknown replay extensions:** rejected because that
  can silently alter dialogue or sever tool-call relationships.

## Consequences and Boundary

A portable conversation can switch among admitted providers without server-side state.
Continuity depends on client-replayed portable dialogue rather than a provider
store. The proxy may reject an input that lacks a proved portable meaning
instead of guessing or silently dropping a required tool relationship.
Encrypted delegation requires an upstream able to interpret its native control
payload; cross-provider decryption is not guaranteed. A visible message envelope
is not proof that its task body survived transport.

This also preserves route mobility: switching Provider Accounts does not depend
on another provider's stored response or opaque item identifier. This mobility
claim excludes encrypted native delegation. Provider-specific recovery consumes
the same portable owner or a strictly smaller derivation; it cannot become a
parallel replay implementation.

## Evidence and Revisit

The [provider-portable Responses specification](../../openspec/specs/provider-portable-responses/spec.md)
owns the admitted replay and control semantics. Its scenarios describe required
behavior, not universal provider interoperability or present delivery evidence.

Revisit when native control-message semantics change, or when the ecosystem
provides a documented interoperable encrypted transport. Require a real
sender-to-recipient content witness, not merely accepted JSON or a healthy listener.
