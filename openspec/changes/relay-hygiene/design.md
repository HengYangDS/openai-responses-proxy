# Relay hygiene after 8.0.0

## Context

- `relay/addressing.resolve` builds the origin from the authority exactly as the path writes it. `API.Example.com`,
  `api.example.com:443` and `api.example.com:0443` are three origins with three cooldowns and three pool keys, and
  all three go to one upstream (M1). Nothing bounds the host's length (M2). The listener's request line is already
  bounded at 65,536 bytes by `http.server`, and the cooldown cache holds at most 1,024 keys that expire.
- `relay/connections.Pool` keeps up to 8 idle connections per `(scheme, host, port)`. An expired connection closes
  only when its own address is acquired again. An origin that is never used again keeps its sockets and its key
  for as long as the listener runs, and the number of addresses has no bound (M3).
- `serving/server.py` refuses a data request from a client that is not local with `send_error(403, ...)`: an HTML
  page from the standard library and no log line (M4). Every other refusal on the data plane is the JSON error
  envelope of `protocol/response.error_payload`.
- A rejected route records the counter and failure class `provider_route_rejected` and logs
  `event=provider_route_rejected` or `event=provider_route_method_rejected`. The client already gets
  `upstream_route_invalid` or `upstream_route_method_not_allowed` (M5). The other `provider_*` names count the
  upstream provider's rate limits and unavailability, which are still provider behavior, and they stay.
- `BaseHTTPRequestHandler.send_response` writes the proxy's `Server` and `Date`. `relay/downstream` then copies
  the upstream's headers, including its own `Server` and `Date`.
- `nox -s full` notifies governance first, but Nox runs the remaining sessions after a failure. On 2026-11-26 a
  check spent 12 minutes after governance's Prettier had refused.
- On 2026-11-27 the predecessor fixture of `Published release compatibility` used 8.0.0's own admission on a 7.0.2
  archive. That cost a second proof and a second GitLab ticket. The failure was reproduced locally with the
  published 7.0.2 assets, so the fixture step can run without registering a service.

## Goals / Non-Goals

**Goals:** each item reaches its end state: one spelling per upstream, idle state bounded by the listener and not by
the clients, one refusal shape and one name per refusal on the data plane, HTTP-correct relayed header fields, and a
check that fails in seconds when governance fails.

**Non-Goals:** the lifecycle control endpoint's refusals, which stay plain text for its local controller; the
`provider_*` names that describe the upstream provider's behavior; Python 3.14, which waits for ETHOS 0.2.0-beta.13.

## Decisions

### D1. One canonical origin

`resolve` writes the authority in the normal form of RFC 3986 sections 6.2.2.1 and 6.2.3:

- The host is lower-cased.
- The port is written in decimal without leading zeros. The scheme's default port, 443 for `https` and 80 for
  `http`, is left out, and port 0 is refused.
- An IPv6 literal is written in the form of RFC 5952, in brackets. Zone identifiers and IPvFuture are refused.
- An IPv4 literal must be four decimal octets. Other all-numeric hosts (`127.1`, `0x7f.0.0.1`, `0177.0.0.1`) are
  refused, because resolvers read them as addresses other than the text.
- Any other host is a DNS name: labels of letters, digits, `-` and `_`, 1 to 63 octets each, at most 253 octets
  in all, with no trailing dot.

The canonical origin keys cooldowns and the pool, is named in log lines, and is the URL the proxy sends, so the
upstream's `Host` is canonical too. The loopback rule for `http` reads the canonical host, so `LOCALHOST` is
loopback. M2 is answered by this grammar: the host is bounded at 253 octets, and the base path stays bounded by
the request line.

Alternatives: refusing a non-canonical spelling instead of normalizing it. Rejected: RFC 3986 defines these
spellings as one URI, and a refusal would fail a client for a difference that the upstream ignores. Leaving the
key raw and documenting AIGW's spelling: rejected, because it keeps M1 for every other client.

### D2. Idle connections bounded by the listener

The pool keeps at most 8 idle connections per address and 32 in all. A release beyond 32 closes the
least recently released connection. `Pool.expire` closes every connection idle for longer than 30 seconds and
drops addresses left empty. The listener calls it from `socketserver`'s own `service_actions` hook, which
`serve_forever` runs on every poll, about twice a second. So within about 30.5 seconds of its last response, an
origin holds no socket and no key, whether or not any request follows, and the pool never holds more than 32.

Alternatives: a reaper thread (rejected: one more thread to start, stop and hand off); expiring only on acquire and
release (rejected: with no traffic, idle sockets stay open); a total cap alone (rejected: idle sockets stay open
until evicted).

### D3. A structured refusal for a client that is not local

A data request that fails the local-client rule gets HTTP 403 with the error envelope: type
`invalid_request_error`, code `local_client_required`, and the message the HTML page carried. The response
closes the connection, because the request body is unread. The proxy logs `event=local_client_required` and
records the counter and failure class of the same name. The control endpoint keeps its plain text.

### D4. One name per refusal

Each data-plane refusal uses its client-facing error code as its counter, failure class and log event:
`upstream_route_invalid`, `upstream_route_method_not_allowed` and `local_client_required`. Two route counters
in `status --json` change name. The individual counter names are not a documented contract, and CHANGELOG names
the change.

### D5. One `Server` and one `Date` on a relayed response

A relayed response begins with `send_response_only` and carries the upstream's header fields, its `Server` and
`Date` included. The proxy adds its own `Date` only when the upstream sent none, as RFC 9110 section 6.6.1 asks
of a recipient that forwards a message. Responses that the proxy makes itself keep its own `Server` and `Date`.

Alternative: dropping the upstream's fields and keeping the proxy's. Rejected: the client would lose the
upstream's identity and clock, which a relay should pass through.

### D6. A check that stops at its first failure

The noxfile sets Nox's own `stop_on_first_error`. `full` still notifies governance first, so a governance failure
ends `mise run check` in seconds. The proof's matrix gate and CI's quality jobs also stop at their first failing
session, and they fail either way.

### D7. The rule from 2026-11-27, written down

CONTRIBUTING requires the following before the proof, whenever a change alters the payload's shape:

- list every reader of another release's archive, the predecessor fixture of `release_compatibility` included;
- run that fixture locally against the published predecessor's assets.

### D8. The small corrections

`lifecycle/projection.verify_payload_manifest` is documented as verifying the payload manifest (M6). The
agent-delivery test writes its expected upstream body out literally, so a projection that changed the
collaboration catalog would fail it (M7).

## Risks / Trade-offs

- **A client that relied on two spellings having separate cooldowns** now shares one cooldown for one upstream.
  That is the purpose of M1.
- **Numeric host forms that worked before are refused.** `https://127.1/` reached the loopback address. The proxy
  now refuses it, and the client can write the address in full.
- **More than 32 active origins** close idle connections that a later request would have reused. Each origin
  still keeps up to 8 while it is active, so the cost is one new connection per eviction.

## Migration

Release 8.0.1. It needs no change from AIGW: AIGW writes lower-case hosts without default ports. An installed 8.0.0
listener hands off to 8.0.1, and the payload shape does not change.
