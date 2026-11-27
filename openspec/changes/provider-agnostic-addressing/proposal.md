## Why

The proxy releases four provider-named routes, each with its base URL, and switches behavior on the route name. A new
provider needs a release, as Suhe.AI needed 7.0.0, although DR-0001 assigns endpoint selection to the client's
control plane. The owner asked whether the proxy should be coupled to named providers, and chose on 2026-11-27 to
remove that coupling while keeping the request projection unchanged.

## What Changes

- **BREAKING** The request path names the upstream: a client's base URL is
  `http://127.0.0.1:<port>/<scheme>/<authority>/<base path>`. The provider-named routes, the provider manifest and
  registry, `--upstream-base-url`, the runtime configuration's `upstream_base_url` and the payload file
  `providers.toml` are removed, with no compatibility route.
- **BREAKING** Data requests require a loopback `Host`, no `Origin` and no Fetch Metadata (`Sec-Fetch-*`), as
  control requests then do too.
- **BREAKING** The route error codes become `upstream_route_invalid` and `upstream_route_method_not_allowed`.
- The DMXAPI HTTP 477 retry and its fingerprint cooldown are removed; an HTTP 477 reaches the client like any other
  client error.
- Rate-limit and unavailability cooldowns, and log lines, are keyed by the upstream origin instead of the route
  name.
- UCloud's plaintext agent-message delivery is removed: measurement M4 in the design showed that UCloud now handles
  Codex's native encrypted collaboration messages.
- The projection to the provider-portable form is unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-portable-responses`: upstream addressing replaces the provider routes; provider-named recovery,
  extension and plaintext-delivery requirements are removed; cooldowns and concurrency are stated per upstream origin.
- `runtime-upgrade`: the installed payload no longer contains `providers.toml`.

## Impact

`src/openai_responses_proxy/providers/` is deleted. `relay/`, `serving/`, `lifecycle/`, `cli/`, `runtime/config.py`,
the release bundle, the hard-coding policy, the performance tools and their tests change. Version 8.0.0. AIGW, the
control plane that writes Codex's base URLs, releases its own change against 8.0.0 before 8.0.0 is installed. Out of
scope: keeping an issuer's own opaque state for that issuer, Anthropic Messages and Chat Completions.
