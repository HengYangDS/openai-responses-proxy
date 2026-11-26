## Why

The owner switches providers within one Codex conversation (2026-11-26, 19:30), and AIGW offers Codex Suhe.AI's
GPT models over Responses. Codex reaches Suhe.AI directly today, so after a switch the request to Suhe.AI still
carries reasoning and item state that another provider issued; the proxy's projection exists to remove such state
before it reaches a different upstream. The proxy serves only the provider routes in its released manifest, so
Suhe.AI needs a route of its own.

The route is interim. A later breaking Change is planned to make the request path name the upstream and to remove
every provider-named route; its design, approved by the owner on 2026-11-26 at 19:56, is not yet in this repository.

## What Changes

- A released route `suheai` to `https://suheai.cn/v1`: `/suheai/v1/responses` and `/suheai/v1/models`, with native
  agent delivery and no wire policy.
- The specification names provider routes in general terms wherever it meant every route, so route-scoped recovery,
  rate-limit isolation, client endpoint ownership and the acceptance sequence cover Suhe.AI; the routes requirement
  keeps the one list. The open Change `terminal-product-convergence` gets the same wording in its switching
  scenario, and the evidence policy points at the specification's acceptance scenario instead of restating it.
- The README and the CHANGELOG list the route.
- Release 7.0.0 carries the route and the **breaking** removal of Windows and Intel macOS that the archived Change
  `drop-windows-and-intel-macos` made.
- The Change includes delivery: it stays active until 7.0.0 is published, installed on this host and accepted in a
  Codex conversation through the route.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-portable-responses`: Suhe.AI is a governed provider route, and requirements that meant every route say
  so.

## Impact

`src/openai_responses_proxy/providers/manifest.toml`, `README.md`, `CHANGELOG.md`,
`tests/providers/test_registry.py`, `docs/evidence/evidence-policy.md` and the `provider-portable-responses` delta of
`terminal-product-convergence`.

- **Installed product:** an installation of 7.0.0 serves the new route; the existing routes and their behavior are
  unchanged.
- **Client side:** AIGW points Codex's Suhe.AI provider at `http://127.0.0.1:8792/suheai/v1`; that change belongs to
  AIGW, not to this repository.
- **Non-goals:** Suhe.AI's Anthropic Messages and Chat Completions endpoints; a wire policy for Suhe.AI, since no
  Suhe.AI Responses exchange has been observed yet and the release policy adds one only for a captured
  incompatibility; the planned redesign itself.
