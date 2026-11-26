## Context

`src/openai_responses_proxy/providers/manifest.toml` is the release-owned provider registry, and `CONTRIBUTING.md`
("Provider extension") makes an ordinary OpenAI-compatible Responses provider one manifest table, with no CLI
command, environment variable, installer case or provider-name switch. AIGW's team file (`manifests/team.toml` in
aigw-cli, read 2026-11-26) gives Suhe.AI the Responses endpoint `https://suheai.cn/v1` and four GPT models on it.

On 2026-11-26 the endpoint answered `GET /v1/models` without a credential with HTTP 401 and a JSON error body, over a
TLS connection verified against the certifi bundle that `uv.lock` pins (2026.10.23), the store the release bundles.
No Suhe.AI Responses exchange has been observed: it needs a client credential, which AIGW's control plane holds and
this repository's checks run without.

## Goals / Non-Goals

**Goals:** a Codex conversation that switches between the proxied DMXAPI route and Suhe.AI passes the proxy on both
sides, so the projection removes the other provider's state before Suhe.AI sees it.

**Non-Goals:** Suhe.AI's Anthropic and Chat endpoints; a Suhe.AI wire policy; changing the provider-named routing
model, which the planned redesign replaces.

## Decisions

### D1 One manifest table

`[providers.suheai]` with `base_url = "https://suheai.cn/v1"`, as `CONTRIBUTING.md` prescribes for an ordinary
provider. The alternative, the installation's single explicit upstream (`--upstream-base-url`, `/upstream/v1`), was
rejected: it names no provider in the route, takes the only explicit slot, and changes the installation's runtime
configuration instead of the released catalog.

### D2 No wire policy and native agent delivery

No Suhe.AI Responses exchange has been observed yet, and the release policy ("Provider changes") adds a wire policy
only for a captured, reproducible incompatibility, so the route starts without one. An incompatibility that the
acceptance (D4) captures is repaired in this Change before its archive, as the release policy requires of an open
obligation.

### D3 The specification names routes in general terms

The manifest owns the provider list. Requirements that meant every route listed three names, so each new route had
to edit them. They now say "another provider route" or "every other provider route"; the routes requirement keeps
the one list, which `tests/providers/test_registry.py` ties to the shipped manifest.

- "Every Responses request is projected to a provider-portable form" is also MODIFIED by `terminal-product-convergence`,
  and a MODIFIED block replaces the whole requirement. Its switching scenario is therefore changed in that Change's
  delta, not here, and the lane rehearses archiving both Changes in either order, as `drop-windows-and-intel-macos`
  did (its task 1.2).
- The main specification's Purpose lists three routes and lies outside the delta operations; the archive commit of
  this Change rewrites it in the same general terms.
- `docs/evidence/evidence-policy.md` restated the acceptance sequence; it now names the specification's scenario, so
  the sequence has one source.

### D4 Delivery and acceptance on the new legs

The release policy keeps a Change that includes delivery active while publication, installation and actual use
consume its increment. The acceptance follows the evidence policy on the legs DMXAPI to Suhe.AI to DMXAPI: an
unchanged conversation completes at least two turns on each leg, with its JSONL prefix and metadata verified after
every leg. AIGW points Codex at the route and runs the conversation; the proxy's maintainer verifies the evidence.
The full sequence through every provider route is the acceptance of the planned redesign's release, as the owner
decided on 2026-11-26 at 19:55 for the acceptance tasks 13.5 to 13.7 of `terminal-product-convergence`.

## Risks / Trade-offs

- **The route is short-lived.** The planned redesign removes every provider-named route, and AIGW re-points its
  clients then. The cost is one manifest table and one README row now.
- **Untested Responses behavior.** It cannot be measured before release here: an exchange needs a client credential
  that only AIGW's control plane holds. The acceptance (D4) is the first measurement. Only the new route is exposed
  to it, since the existing routes' manifest tables are unchanged, and AIGW rolls back by pointing Codex's Suhe.AI
  provider at `https://suheai.cn/v1` again.

## Migration Plan

Released in 7.0.0. After installation, AIGW points Codex's Suhe.AI provider at `http://127.0.0.1:8792/suheai/v1`;
the rollback is the direct endpoint above.
