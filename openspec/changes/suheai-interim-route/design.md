## Context

`src/openai_responses_proxy/providers/manifest.toml` is the release-owned provider registry, and `CONTRIBUTING.md`
("Provider extension") makes an ordinary OpenAI-compatible Responses provider one manifest table, with no CLI
command, environment variable, installer case or provider-name switch. AIGW's team file (`manifests/team.toml` in
aigw-cli, read 2026-11-26) gives Suhe.AI the Responses endpoint `https://suheai.cn/v1` and four GPT models on it.

On 2026-11-26 the endpoint answered `GET /v1/models` without a credential with HTTP 401 and a JSON error body, over a
TLS connection verified against the certifi bundle that the lane locks (2026.10.23), the same store the release
bundles.

## Goals / Non-Goals

**Goals:** a Codex conversation that switches between the proxied DMXAPI route and Suhe.AI passes the proxy on both
sides, so the projection removes the other provider's state before Suhe.AI sees it.

**Non-Goals:** Suhe.AI's Anthropic and Chat endpoints; a Suhe.AI wire policy; changing the provider-named routing
model, which the issuer-boundary design replaces later.

## Decisions

### D1 One manifest table

`[providers.suheai]` with `base_url = "https://suheai.cn/v1"`, as `CONTRIBUTING.md` prescribes for an ordinary
provider. The alternative, the installation's single explicit upstream (`--upstream-base-url`, `/upstream/v1`), was
rejected: it names no provider in the route, takes the only explicit slot, and changes the installation's runtime
configuration instead of the released catalog.

### D2 No wire policy and native agent delivery

No Suhe.AI response has been observed that needs a policy or plaintext agent delivery. A policy is added only for a
captured, reproducible wire incompatibility (release policy, "Provider changes").

## Risks / Trade-offs

- **The route is short-lived.** The issuer-boundary Change removes every provider-named route; AIGW re-points its
  clients then. The cost is one manifest table and one README row now.
- **Untested Responses behavior.** No Codex turn has passed through the route before release; the installed
  acceptance runs one turn and one conversation that switches between DMXAPI and Suhe.AI after AIGW re-points
  Codex.

## Migration Plan

Released in 7.0.0. After installation, the aigw session points Codex's Suhe.AI provider at
`http://127.0.0.1:8792/suheai/v1`.
