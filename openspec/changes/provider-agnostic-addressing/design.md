# Provider-agnostic Responses adapter (8.0.0)

The owner approved this design on 2026-11-27. It narrows the design of 2026-11-26, which also kept each issuer's
own opaque state for that issuer and was not built: on 2026-11-27 the owner chose to build its provider-agnostic
half now and to leave issuer-aware state out (Approaches).

## Context

### What the proxy is for

The owner switches providers within one Codex conversation (owner, 2026-11-26). A Responses conversation
carries opaque state that only its issuer can read: encrypted reasoning, compaction items, stored-response
references and stored item identifiers. Another upstream refuses that state. The proxy projects every request to a
provider-portable form before upstream I/O, so any upstream accepts it (DR-0002, specification
`provider-portable-responses`). This adapter is needed only for Responses: Anthropic Messages degrades
unreadable thinking on the server side, and Chat Completions carries no issuer-bound state.

### Where the product departs from that purpose

1. **Provider names in the release.** `providers/manifest.toml` ships four routes (`aihubmix`, `dmxapi`, `suheai`,
   `ucloud`), each with its base URL. A fifth provider needs a release, as Suhe.AI needed 7.0.0. DR-0001 assigns
   endpoint selection to the client control plane, and the hard-coding control `network-provider-route` makes the
   manifest a released product catalog.
2. **Behavior keyed by provider name.** Four kinds of behavior switch on the route name:
   - the DMXAPI HTTP 477 retry and its request-fingerprint cooldown (`providers/policies/dmxapi.py`,
     `relay/wire_recovery.py`, and the generic exclusion of 477 in `relay/exchange.py`);
   - UCloud's plaintext agent-message delivery (`protocol/agent_delivery.py`, selected by the manifest and applied in `relay/`);
   - the rate-limit and unavailability cooldown keys (`relay/cooldown.py`, `provider:<name>`);
   - the `provider=` field of every log line.

### What the installed product shows

The installed proxy's four retained log files cover 2026-11-19 to 2026-11-27 (read on 2026-11-27).
They hold 21,414 Responses requests:

- 21,404 on `dmxapi`;
- 4 on `ucloud`, 4 on `aihubmix` and 2 on `suheai`, all from the acceptance and measurements of 2026-11-27;
- no HTTP 477 at all.

The window is eight days; it does not show that a 477 never happens.

### What the measurements of 2026-11-27 showed

The measurements are recorded in the archived Change `dmxapi-pass-through`.

- DMXAPI and Suhe.AI accepted their own replayed reasoning items without an error.
- In three alternating measurements, no provider reasoned on any path, its own endpoint included. These covered
  DMXAPI with `gpt-6.2-sol-cdx` and UCloud and AIHubMix with `gpt-6.2-sol`. So whether keeping an issuer's own
  state, or any request shape, changes reasoning is unmeasured.
- The path addressing of D1 ran through a throwaway build all morning, against DMXAPI and Suhe.AI
  (`/https/www.dmxapi.cn/v1`, `/https/suheai.cn/v1`).

## Goals / Non-Goals

**Goals**

1. The proxy names no provider. It serves any HTTPS Responses upstream that the request path names, and a new
   provider needs no release.
2. No behavior depends on a provider name. Behavior that is kept is keyed by the upstream origin; behavior with no
   evidence of use is removed.
3. The request projection is unchanged: every request is projected to the provider-portable form, and replayed
   opaque state is stripped as today (DR-0002).
4. AIGW writes the new base URLs for every Responses provider that Codex switches among. AIGW owns that change; this
   design states the contract.

**Non-Goals**

- Keeping an issuer's own opaque state for that issuer (the part of the 2026-11-26 design that was not built). Section "Where
  issuer-aware state would join" states where it would join.
- Anthropic Messages and Chat Completions: no route; `/…/messages` and chat paths stay refused.
- Routing, fallback, load balancing, billing or organization policy (DR-0001 rejects a general gateway).
- Reading or writing client configuration, credentials or conversation state.
- Compatibility for the provider-named routes, `--upstream-base-url` or the `providers.toml` payload file. None is
  kept (owner, 2026-11-06 and 11-10: no compatibility code).

**Anti-goals** (a failure even if everything else works)

- The proxy stores a credential or any request or response content.
- A second registry of providers beside AIGW's.
- A fully sent request is sent again without the upstream having answered it.

## Constraints

- DR-0001: the control plane owns endpoints and credentials; the proxy owns outbound protocol normalization, its
  payload and its service lifecycle.
- The listener binds loopback only. Control requests already require a loopback `Host` and no `Origin`
  (`relay/admission.py`, `is_local_controller`).
- Python 3.12 to 3.14 until the separate Python 3.14 Change after ETHOS 0.2.0-beta.13. ETHOS governance applies, and
  authored Markdown stays within 500 non-blank lines.
- The main specification `provider-portable-responses` holds requirements that this design reverses, among them
  "Provider routes admit only exact Responses and model-catalog targets", "Provider-specific recovery is
  route-scoped", "Provider wire differences are isolated", "Provider extension is adapter-only" and "Plaintext agent
  delivery is selected before generation". No other Change is active.

## Approaches

1. **Chosen (owner, 2026-11-27): provider-agnostic addressing; projection unchanged.** The request path names
   the upstream, and provider-named behavior is removed or keyed by origin. It meets the owner's question of
   2026-11-26 (should the proxy be coupled to named providers?) without depending on any measurement except M4.
2. **Deferred: the full 2026-11-26 design, adding issuer-aware state.** Its benefit, keeping reasoning across turns,
   could not be measured on 2026-11-27, and 7.0.1 showed the cost of building on an unmeasured premise.
3. **Deferred: measure first, then decide.** This needs a period and a model in which a provider's own endpoint
   reasons, which none did in the alternating windows; it delays the provider-agnostic half for no gain to it.
4. **Rejected: the provider registry moved into the proxy's configuration.** It keeps two registries of providers.

## Decisions

### D1. The request path names the upstream

A client's base URL is `http://127.0.0.1:<port>/<scheme>/<authority>/<base path>`, for example
`http://127.0.0.1:8792/https/suheai.cn/v1`. The proxy accepts exactly
`/<scheme>/<authority>/<segment>*/(responses|models)` with an optional query, and forwards to
`<scheme>://<authority>/<segment>*/<resource>?<query>`.

- `scheme` is `https`. `http` is accepted only when the authority is a loopback address, for tests and local
  upstreams.
- `authority` is a host name or IP literal with an optional port, without user information. Segments keep today's
  strict quoting rule: the quoted path must equal the path.
- Data requests gain the control requests' admission rule: loopback `Host` and no `Origin`. The proxy forwards to
  any HTTPS host that a local client names, with that client's own credential; the rule keeps a web page from
  using it.
- Removed: `providers/manifest.toml`, `Registry`, `Profile` and the policy package; `--upstream-base-url` and the
  runtime configuration's `upstream_base_url`; `providers.toml` in the payload (it leaves the bundle inventory and
  `serving_payload_sha256`). The hard-coding control `network-provider-route` is retired with the manifest.
- The upstream origin (scheme, authority, base path, never the query) replaces the provider name in log lines
  (`upstream=`) and in cooldown keys. Error codes `provider_route_not_found` and
  `provider_route_method_not_allowed` become `upstream_route_invalid` and `upstream_route_method_not_allowed`.

### D2. Provider-named behavior

- **DMXAPI HTTP 477 retry and fingerprint cooldown:** removed, together with the generic exclusion of 477 in
  `relay/exchange.py`, so a 477 is relayed like any other client error. Evidence: no 477 among 21,414 requests in
  eight days. If an empty-response failure recurs, its recovery is keyed by the response, not by the provider.
- **Rate-limit and unavailability cooldowns:** kept as they are, keyed by upstream origin. DR-0003 is amended from
  "route" to "upstream origin".
- **UCloud plaintext agent delivery:** removed, with its tests and its requirement. Measurement M4 showed that
  UCloud now handles Codex's native encrypted collaboration messages. Every upstream receives the client's native
  delivery, and D1's grammar admits no parameter.

### D3. The control plane's contract (owned by AIGW)

AIGW writes the D1 base URL for every Responses provider of every client that switches providers within a
conversation (Codex today). It stops writing provider-named proxy routes, and the base URL carries nothing beyond D1's
grammar. The example in AIGW's `internal/cli/client/edit.go` (`--via http://127.0.0.1:8792/v1`) names a
path the proxy already refuses; it changes with this contract. AIGW's maintainer owns that change and its release.

### D4. Specifications, decisions and documents

- `provider-portable-responses` keeps its projection requirements unchanged. Its provider-named requirements are
  replaced:
  - one requirement for D1's addressing and admission;
  - one requirement that no generic module branches on a provider and that a new provider needs no release;
  - the cooldown requirements reworded to the upstream origin;
  - the plaintext requirement removed (D2).
- DR-0001 gains a consequence: the proxy forwards to any client-named HTTPS origin, still without routing,
  fallback or policy. DR-0003 is amended as in D2. DR-0002 is unchanged.
- README, the architecture document, CHANGELOG (`Breaking`), `VERSION` 8.0.0.

### D5. The payload shape and the journey from 7.0.2

The payload of 8.0.0 holds no `providers.toml`. A 7.0.2 listener checks a successor's payload against its own
inventory, which requires that file, so 7.0.2 cannot hand off to 8.0.0; 8.0.0 does not accept 7.0.2's payload
manifest either. Shipping an inert manifest for one release would be compatibility code, which the project does not
keep; 5.0.0 and 6.0.0 met the same choice and answered it the same way.

- 8.0.0 refuses a 7.0.2 installation and changes nothing. 7.0.2's own `uninstall --purge` removes it, and 8.0.0
  then installs fresh.
- The release workflow's published-predecessor job proves this journey against the published 7.0.2, in place of
  the handoff upgrade and the cross-release disposal test, which a predecessor of another payload shape cannot run.
- The next release, whose predecessor 8.0.0 shares the payload shape, restores both from this Change's parent
  commit. Its Change owns that.

The maintainer's call under the project's rule against compatibility code, open to the owner's correction.

## Where issuer-aware state would join

This is not built in 8.0.0. D1's upstream origin is the key that issuer-aware state needs, and there are three
join points:

- the projection call in `relay/responses.py`, which would choose between the client's body and the portable
  shape;
- the response paths that already see every event and body (`relay/sse.py` `restore_event`, and
  `relay/downstream.py`), which would record provenance;
- the HTTP-error path in `relay/exchange.py`, which would hold one portable re-send.

The precondition, per DR-0002's revisit condition: a measurement that alternates the shapes within one period in
which the provider's own endpoint reasons.

## Measurements before code

- **M4** (D2): does UCloud accept native encrypted agent content? This is a yes-or-no protocol check, not a quality
  comparison, so it needs no alternation. AIGW's maintainer ran it on 2026-11-27, with the owner's approval of the
  spend: two turns of codex-cli 0.172.0 direct to `https://api.modelverse.cn/v1`, model `gpt-6.2-sol`, the
  `collaboration` tool namespace and a private Codex home, so no saved client configuration changed.
  - Turn 1: the parent spawned a child with an encrypted `message` argument; the child answered. The child also
    inherited the parent's history, which held the text in plain form, so this turn alone proves no decryption.
  - Turn 2: the parent sent a follow-up whose `message` argument was ciphertext, carrying a text that appears
    nowhere in plain form in the child's history. The child answered with its exact reversal.
  - Both turns completed with no HTTP error. Result: pass for this model and client version; UCloud no longer
    needs plaintext delivery.

No other measurement is needed: the projection is unchanged.

## Risks / Trade-offs

- **Cutover breaks running clients.** Old route URLs stop working at 8.0.0; a Codex process started before AIGW
  re-projects its configuration keeps the old URL. Mitigation: one announced cutover (Migration); no compatibility
  route.
- **Open forwarder.** Any local process can reach any HTTPS host through the proxy with its own credential. That
  equals what it can do directly; the `Host`/`Origin` rule closes the browser path.
- **Host names in the log.** `upstream=` records each origin a client names; the log already records paths, and
  the origin carries no credential or query.
- **Removing the 477 policy on eight days of data.** All of that traffic was DMXAPI; a recurrence costs a
  client-visible error until a response-keyed rule is added.
- **Reasoning still stripped every turn.** As today; the owner accepted this with Approach 1.
- **No native rollback across the cutover.** 8.0.0 is installed fresh (D5), so it retains no 7.0.2 generation.
  The way back is a fresh install of the signed 7.0.2, after the listener has been down for the seconds between
  purge and install.
- **Test churn.** 35 test files name a provider. They move to origin-addressed fixtures; the projection tests do not
  change in substance.

## Migration Plan

1. **M4**, done on 2026-11-27; its result is in D2.
2. **This Change, released as 8.0.0.** Publishing 8.0.0 changes nothing on this host. AIGW's maintainer builds and
   releases AIGW's projection change against the published 8.0.0 and the D3 contract.
3. **Cutover,** once both are released. The owner is told the window. 7.0.2's `uninstall --purge` removes the
   installation, 8.0.0 is installed fresh (D5), AIGW re-projects, and the owner restarts the Codex processes;
   conversations are untouched. The listener is down between purge and install. Installing 8.0.0 before AIGW's
   release is refused by this plan, because 8.0.0 refuses every URL clients use today.
4. **Acceptance, then archive.** The Change closes after the cutover, so its completion follows AIGW's
   release. The maintainer's call:
   - The owner's own next Codex turns on DMXAPI through the new URL are the live check, read from the proxy log;
     they cost nothing extra.
   - Each of the other three origins (UCloud, AIHubMix, Suhe.AI) gets one request without a token through the new
     URL. Its upstream's own authentication error proves the address, the admission rule and the projection
     without spending tokens.

Rollback: the installer refuses a downgrade, and a fresh 8.0.0 installation retains no 7.0.2 generation (D5). The
way back is 8.0.0's `uninstall --purge`, then a fresh install of the signed 7.0.2 from its Release; AIGW projects the
old routes again in the same step.

## Open Questions

- The maintainer's call, open to the owner's correction: the Change name `provider-agnostic-addressing`, and the new error codes in D1.
