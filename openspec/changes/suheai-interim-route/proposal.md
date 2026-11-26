## Why

The owner switches providers within one Codex conversation (2026-11-26, 19:30), and AIGW offers Codex Suhe.AI's
GPT models over Responses. Codex reaches Suhe.AI directly today, so a switch to Suhe.AI carries reasoning and item
state that another provider issued to an upstream that cannot read it: the failure that the proxy's projection
exists to absorb. The proxy serves only the provider-named routes in its released manifest, so Suhe.AI needs a route
of its own until the request path names the upstream, as the issuer-boundary design that the owner approved on
2026-11-26 at 19:56 plans for a later breaking release.

## What Changes

- A released route `suheai` to `https://suheai.cn/v1`: `/suheai/v1/responses` and `/suheai/v1/models`, with native
  agent delivery and no wire policy.
- The README lists the route among the client routes.
- The route is interim: it leaves with the other provider-named routes when the request path names the upstream.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-portable-responses`: the governed provider routes include Suhe.AI.

## Impact

`src/openai_responses_proxy/providers/manifest.toml`, `README.md`, `tests/providers/test_registry.py` and
`CHANGELOG.md`.

- **Installed product:** an installation of the release that carries this Change serves the new route; the existing
  routes and their behavior are unchanged.
- **Client side:** AIGW points Codex's Suhe.AI provider at `http://127.0.0.1:8792/suheai/v1`; that change belongs to
  AIGW, not to this repository.
- **Non-goals:** Suhe.AI's Anthropic Messages and Chat Completions endpoints; a wire policy for Suhe.AI, since no
  incompatibility of its own has been observed; the issuer-boundary redesign itself.
