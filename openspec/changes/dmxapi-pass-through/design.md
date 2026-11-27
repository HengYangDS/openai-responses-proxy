## Context

The section "Outcome" at the end supersedes the conclusion that this section and D1 to D3 rest on; they
remain as the record of 7.0.1.

The proxy's one portability grammar (`protocol/replay/projection.py`, DR-0002) rebuilds every request: it forces
`store=false`, drops `previous_response_id`, `conversation` and `prompt_cache_key`, removes
`reasoning.encrypted_content` from `include`, removes replayed reasoning items and the identifiers of message items.
Measurements on 2026-11-27 (records: the issuer-boundary design's section "Results on DMXAPI", the spike's
`spike.jsonl`, AIGW's `build/evidence/phase3-spike-dmxapi-20261127/`) show, for DMXAPI and Codex 0.172:

| Request sent to DMXAPI                                    | Requests | Reasoning tokens                                        |
| --------------------------------------------------------- | -------- | ------------------------------------------------------- |
| The client's body, conversation held on DMXAPI            | 7        | every response had a reasoning item; turns 239, 337, 59 |
| Portable projection (cache key dropped or kept)           | 16       | 0                                                       |
| Only message `id`s removed                                | 2        | 0                                                       |
| Only `prompt_cache_key` and the `include` entry removed   | 2        | 0                                                       |
| The client's body, history holds a Suhe.AI reasoning item | 5        | 0, no error                                             |

Every changed body completed with HTTP 200, so the loss is silent. `json.dumps` of a real Codex 0.172 body
reproduces its bytes exactly, and the request headers are the same on both paths (local capture without egress), so
the proxy's re-encoding and headers are ruled out; why DMXAPI behaves so is not observable.

## Goals / Non-Goals

**Goals:** a conversation held on DMXAPI reasons through the proxy; no request that the proxy refuses today is
admitted; behavior of the other routes is unchanged.

**Non-Goals:** reasoning on DMXAPI after a provider switch (DMXAPI does not reason then, measured); the
issuer-boundary redesign; a failure in a stream that has already sent content, which no attempt may repeat.

## Decisions

### D1. A declared request shape per route

The manifest gains `request_shape = "portable" | "pass-through"` (default `portable`), carried on `Profile`. The
relay reads the field, never a provider name, as "Provider extension is adapter-only" requires. DMXAPI declares
`pass-through`. `Profile` itself refuses pass-through with `agent_message_delivery = "plaintext"`, so neither the
manifest nor code that builds a route directly can combine them: plaintext delivery rewrites the body, which would
defeat the declaration.

### D2. What is forwarded unchanged, and only once

The relay runs the portable projection on every request first. A rejection is answered locally, as today, so the
item model stays the one authority ("Responses semantics have one classification and projection authority",
"Unproved replay shapes fail closed"). On a pass-through route, a request whose `store` is `false` and that has no
`previous_response_id`, `conversation` or `item_reference` item is stateless: its first attempt carries the
client's original bytes. Any other request is sent in the projected form, because forwarding it would let the
upstream store, continue or look up state. `prompt_cache_key` and `include` stay as the client wrote them: removing
either alone turns DMXAPI's reasoning off (table above).

The projection also normalizes the request beyond state: empty tool outputs get a marker, an empty assistant
placeholder is omitted, local-shell pairs are removed, assistant history becomes Easy Input strings, later tool
results become `tool_delivery` messages. The client's bytes skip all of that, and a smaller projection is not
possible: every measured change, however small, turned reasoning off. So the client's bytes are sent at most once
per request. The exchange keeps the projected request, and the first time it sends again (a transient retry,
DMXAPI's 477 retry, a stream reopen after a pre-content failure, or the re-send of D3), it sends the projection.
After a failure, the request therefore takes exactly the path that 7.0.0 took, one round trip later. Requirements
that describe the outbound form now say "projected request"; the delta modifies the three whose text named the
outbound copy. The delta was dropped in 7.0.2; its last form is at commit 8094cf82.

### D3. One projected attempt after a refusal of the client's bytes

An HTTP 400 to the client's bytes means the upstream generated nothing, so a re-send is safe. The exchange answers
any such 400 by sending the projected request once, before any other recovery; a refusal of the projection then
follows the portable route's recovery unchanged. Limiting the re-send to the structured code
`invalid_encrypted_content` was rejected after review: a 400 for a shape that only the projection normalizes would
then be relayed, or would start a recovery that drops history, where 7.0.0 completed. The re-send is counted as
`pass_through_projected_resends` and uses one attempt of the existing per-request budget. This does not conflict
with "Request-changing recovery uses structured errors": the projected request is not a change of the projected
request, it is the form 7.0.0 sent first. An encrypted agent task refused in the projected request is still relayed
without a further request. The counters of stripped items count only projected requests that were sent.

### D4. Withdraw the pass-through route

The owner withdrew the route on 2026-11-27 at 12:51, choosing 7.0.2 over keeping the code with corrected
documents. Reason: the route had no measured benefit (section "Outcome"), it added a second send path and a per-route attribute that
names a provider's behavior, against the direction of the issuer-boundary redesign, and the project's rule admits no
entity that is not necessary. 7.0.2 restores the source of 7.0.0 apart from the decision record and release
metadata, so the main specification's requirement that every Responses request is projected applies to every
route again, with the tests that 7.0.0 shipped for it. The specification delta is dropped and the Change declares
`skip_specs`. Rollback: reinstall 7.0.1.

## Risks / Trade-offs

- **A changed answer without an error.** If DMXAPI accepts the client's bytes but answers worse than it would the
  projection, no failure is visible. The measurements saw the opposite: Codex's own bodies, with tool calls and
  reasoning items, completed and reasoned in every request.
- **A foreign item refused with an unknown code.** UCloud and AIHubMix reasoning items in a body sent to DMXAPI are
  not measured. Any 400 for them is answered by the projection; a refusal in a stream that starts with content is
  not.
- **Duplicate JSON keys.** The proxy parses a duplicate key as its last value but forwards the bytes; an upstream
  that keeps the first value could read a field the proxy did not check. The client can send those bytes to the
  upstream directly, so the proxy is not a boundary against its own client; it is not checked, to avoid a second
  parse of every DMXAPI request.
- **The original body reaches the upstream.** It holds the client's own identifiers and opaque items, which the
  client sent on purpose; nothing is added and no client state is read.

## Migration Plan

Release 7.0.1 and install it after telling the owner the window. Acceptance: a new Codex conversation on DMXAPI
through the installed proxy reports reasoning tokens. Rollback: reinstall 7.0.0. The acceptance failed; 7.0.2
withdraws 7.0.1 and is installed the same way. Its acceptance: a DMXAPI request through the installed proxy is
logged as projected, without the `unchanged` note.

## Outcome (2026-11-27)

7.0.1 was installed at 11:47 and behaved as designed: the proxy log shows every DMXAPI request of the acceptance sent
`unchanged` and completed, with no re-send. The fix was still not accepted. `codex exec` reports a thread's running
token total in each turn, so the per-turn values below, and those in the Context table, are differences of those
totals. Records (local to this host, not durable): AIGW's `build/runtime/work/phase3-spike-dmxapi/runs/accept-701/`
and `runs/alt-*/` (turn records and the throwaway build's request log) and the installed proxy's operational log for
11:57–11:59 and 12:12–12:13. The throwaway build is `git archive` of dev at 9f575e8d (7.0.0 source) with the spike's
patch.

| Run (gpt-6.2-sol-cdx, effort xhigh, one new conversation each) | Time        | Reasoning tokens per turn |
| -------------------------------------------------------------- | ----------- | ------------------------- |
| Acceptance through 7.0.1, two turns                            | 11:57–11:59 | 31, 0                     |
| A1 direct to `https://www.dmxapi.cn/v1`, no proxy              | 12:12       | 0                         |
| B1 through 7.0.1, unchanged                                    | 12:12       | 0                         |
| C1 through the throwaway build, portable projection            | 12:12       | 0                         |
| C2 portable projection                                         | 12:13       | 0                         |
| B2 through 7.0.1, unchanged                                    | 12:13       | 0                         |
| A2 direct, no proxy                                            | 12:13       | 0                         |

All runs completed without an error. The only DMXAPI conversation with substantial reasoning that day was the
first unchanged run at 08:18 (239, 337 and 59 tokens per turn). Every changed shape was measured after it, from
08:20 on, so the morning's table compared shapes at different times and does not show that the shape decides whether
DMXAPI reasons. In the alternating order A, B, C, C, B, A, which removes that confound, no path reasoned, the
provider's own endpoint included: unchanged forwarding brought no benefit, and whether DMXAPI reasons varied over the
day for reasons outside the proxy. Whether the shape matters at a time when DMXAPI does reason was not measured.
