## Context

The proxy's one portability grammar (`protocol/replay/projection.py`, DR-0002) rebuilds every request: it forces
`store=false`, drops `previous_response_id`, `conversation` and `prompt_cache_key`, removes
`reasoning.encrypted_content` from `include`, removes replayed reasoning items and the identifiers of message items.
Measurements on 2026-11-27 (records: the issuer-boundary design's section "Results on DMXAPI", the spike's
`spike.jsonl`, AIGW's `build/evidence/phase3-spike-dmxapi-20261127/`) show, for DMXAPI and Codex 0.172:

| Request sent to DMXAPI                                    | Requests | Reasoning tokens                                         |
| --------------------------------------------------------- | -------- | -------------------------------------------------------- |
| The client's body, conversation held on DMXAPI            | 7        | every response had a reasoning item; turns 239, 576, 635 |
| Portable projection (cache key dropped or kept)           | 16       | 0                                                        |
| Only message `id`s removed                                | 2        | 0                                                        |
| Only `prompt_cache_key` and the `include` entry removed   | 2        | 0                                                        |
| The client's body, history holds a Suhe.AI reasoning item | 5        | 0, no error                                              |

Every changed body completed with HTTP 200, so the loss is silent. `json.dumps` of a real Codex 0.172 body
reproduces its bytes exactly, and the request headers are the same on both paths (local capture without egress), so
the proxy's re-encoding and headers are ruled out; why DMXAPI behaves so is not observable.

## Goals / Non-Goals

**Goals:** a conversation held on DMXAPI reasons through the proxy; no request that the proxy refuses today is
admitted; behavior of the other routes is unchanged.

**Non-Goals:** reasoning on DMXAPI after a provider switch (DMXAPI does not reason then, measured); the
issuer-boundary redesign; refusals that arrive as stream events.

## Decisions

### D1. A declared request shape per route

The manifest gains `request_shape = "portable" | "pass-through"` (default `portable`), carried on `Profile`. The
relay reads the field, never a provider name, as "Provider extension is adapter-only" requires. DMXAPI declares
`pass-through`. Pass-through with `agent_message_delivery = "plaintext"` fails the load: plaintext delivery rewrites
the body, which would defeat the declaration.

### D2. What is forwarded unchanged

The relay runs the portable projection on every request first. A rejection is answered locally, as today, so the
item model stays the one authority ("Responses semantics have one classification and projection authority",
"Unproved replay shapes fail closed"). On a pass-through route, a request whose `store` is `false` and that has no
`previous_response_id` or `conversation` is then sent as the client's original bytes. Any other request is sent in
the projected form, because forwarding it would let the upstream store or continue state. `prompt_cache_key` and
`include` stay as the client wrote them: removing either alone turns DMXAPI's reasoning off (table above).

The alternative of a smaller projection (keep identifiers, drop only foreign reasoning) is rejected: every measured
change, however small, turned reasoning off.

### D3. One portable re-send on a structured refusal of state

The exchange keeps the projected body beside the unchanged one. On any HTTP 400 answer to the unchanged body, the
exchange's next attempt becomes the projected body: every request-changing recovery, all of which answer a 400
(capacity, input variant, response failed), then starts from the projected form, as DR-0002 requires. Those recovery
functions also re-project their input themselves, so this does not depend on the order of checks. When the 400 has
type `invalid_request_error` and code `invalid_encrypted_content` (the classification `relay/telemetry.py` already
owns), the projected body is sent once and counted as `pass_through_projected_resends`. It is the only refusal of
state whose structure is known; no upstream returned any refusal in the measurements, so others are relayed. The
re-send uses one attempt of the existing per-request budget. A transient retry (5xx, transport, DMXAPI's 477, a
pre-content `response.failed` in a stream) re-sends the body of the current attempt, as it does today.

## Risks / Trade-offs

- **A foreign item refused with an unknown code.** UCloud and AIHubMix reasoning items in a body sent to DMXAPI are
  not measured. If DMXAPI refuses them with a code other than `invalid_encrypted_content`, the turn fails where today
  it completes without reasoning. Rollback is reinstalling 7.0.0.
- **The original body reaches the upstream.** It holds the client's own identifiers and opaque items, which the
  client sent on purpose; nothing is added and no client state is read.

## Migration Plan

Release 7.0.1 and install it after telling the owner the window. Acceptance: a new Codex conversation on DMXAPI
through the installed proxy reports reasoning tokens. Rollback: reinstall 7.0.0.
