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

**Non-Goals:** the lifecycle control endpoint's refusals, which keep the standard library's error page, since their one
intended caller is the proxy's own lifecycle controller and not a model client; the `provider_*` names that describe
the upstream provider's behavior; Python 3.14, which waits for ETHOS 0.2.0-beta.13.

## Decisions

### D1. One canonical origin

`resolve` writes the authority in the normal form of RFC 3986 sections 6.2.2.1 and 6.2.3:

- The host is lower-cased.
- The port is written in decimal without leading zeros. An empty port and the scheme's default port, 443 for
  `https` and 80 for `http`, are left out, and port 0 is refused.
- An IPv6 literal is written in the form of RFC 5952, in brackets. Zone identifiers and IPvFuture are refused.
- A host whose last label is a number (decimal, or hexadecimal after `0x`) must be four decimal octets. Other
  such hosts (`127.1`, `0x7f.0.0.1`, `0177.0.0.1`, `api.example.123`) are refused, because resolvers and the WHATWG
  URL Standard read them as IPv4 addresses other than the text.
- Any other host is one fully qualified DNS name: labels of letters, digits, `-` and `_`, 1 to 63 octets each, at
  most 253 octets in all, and no trailing dot. DNS carries `api.example.com.`, but it is a second spelling of
  `api.example.com` that RFC 3986 does not make equivalent, and certificates name the host without the dot.

The canonical origin keys cooldowns and the pool, is named in log lines, and is the URL the proxy sends, so the
upstream's `Host` is canonical too. The loopback rule for `http` reads the canonical host, so `LOCALHOST` is
loopback. M2 is answered by this grammar: the host is bounded at 253 octets, and the base path stays bounded by
the request line.

Alternatives: refusing a non-canonical spelling instead of normalizing it. Rejected: RFC 3986 defines these
spellings as one URI, and a refusal would fail a client for a difference that the upstream ignores. Normalizing a
trailing dot away: rejected, because RFC 3986 does not define it as the same URI, and a refusal tells the client
which spelling to write. Leaving the
key raw and documenting AIGW's spelling: rejected, because it keeps M1 for every other client.

### D2. Idle connections bounded by the listener

The pool keeps at most 8 idle connections per address and 32 in all. A release beyond 32 closes the
least recently released connection. `Pool.expire` closes every connection idle for longer than 30 seconds and
drops addresses left empty. The listener calls it from `socketserver`'s own `service_actions` hook, which
`serve_forever` runs on every poll, about twice a second. So within about 30.5 seconds of its last response, an
origin holds no socket and no key, whether or not any request follows, and the pool never holds more than 32. The
pool closes a connection without letting a socket error out: an error that escaped `expire` would leave
`serve_forever` and stop the listener.

Alternatives: a reaper thread (rejected: one more thread to start, stop and hand off); expiring only on acquire and
release (rejected: with no traffic, idle sockets stay open); a total cap alone (rejected: idle sockets stay open
until evicted).

### D3. A structured refusal for a client that is not local

A data request that fails the local-client rule gets HTTP 403 with the error envelope: type
`invalid_request_error`, code `local_client_required`, and the message the HTML page carried. The response
closes the connection, because the request body is unread. The proxy counts every such refusal under
`local_client_required`.

Any local web page can make the proxy refuse it, as often as it likes. So the refusal is not recorded as the last
failure, which would let a page hide a real upstream fault in `status --json`, and its log line is bounded: one
`event=local_client_required` line per minute at most, with `refused=<n>` counting the refusals since the previous
line, so a page cannot rotate the log's real lines away. The bound is `operational_log.Throttle`; this event is its
only user. The control endpoint, which a page can reach the same way, keeps the standard library's error page and
writes no line. Alternatives: a line per refusal (rejected for the reason above); no line at all (rejected: M4
found the refusal invisible in the log, and a count per minute keeps that evidence without the flood).

### D4. One name per refusal

The three local refusals use their client-facing error code as their counter: `local_client_required`,
`upstream_route_invalid` and `upstream_route_method_not_allowed`. The two route refusals, which only a local client
reaches, also use it as their failure class and log event. Two route counters in `status --json` change name. The
individual counter names are not a documented contract, and CHANGELOG names the change. The cooldown refusals keep
their `provider_*` names, as the Non-Goals say.

### D5. HTTP-correct relayed header fields

An upstream error, a stream and a model catalog pass on the upstream's header fields. Each begins with
`send_response_only` and carries those fields, the upstream's `Server` and `Date` included. The proxy adds its own
`Date` only when the upstream sent none, as RFC 9110 section 6.6.1 asks of a recipient that forwards a message.
Responses that the proxy writes itself keep its own `Server` and `Date`; a validated non-stream Responses body is
one of them, because the proxy sends it as a local payload and passes on none of the upstream's fields.

Alternative: dropping the upstream's fields and keeping the proxy's. Rejected: the client would lose the
upstream's identity and clock, which a relay should pass through.

Three more corrections make the relayed fields HTTP-correct, in the same place:

- `downstream.end_to_end_fields` is the one filter for both directions. It drops the connection-specific fields of
  RFC 9110 section 7.6.1 (`Connection`, `Proxy-Connection`, `Keep-Alive`, `Transfer-Encoding`, `Upgrade`, and the
  field of section 10.1.4 that names the transfer codings a client accepts), every field that `Connection` names,
  the proxy-authentication fields, `Trailer` (the old list spelled it `trailers`; the proxy re-frames a body and
  sends no trailer), and the fields that each side frames or negotiates itself (`Host`, `Content-Length`,
  `Accept-Encoding`).
- A reopened stream is delivered with its own fields: `sse.relay` passes the stream whose first event it writes to
  `send_headers`. Before, the fields of the first, failed attempt went out with the reopened stream's content.
- A model catalog answered with 204 or 304 is relayed with no body and no chunked framing (RFC 9110 sections
  15.3.5 and 15.4.5). Before, the proxy wrote `Transfer-Encoding: chunked` and a last chunk, which a kept-alive
  client reads as the start of its next response.

### D6. A check that stops at its first failure

The noxfile sets Nox's own `stop_on_first_error`. `full` still notifies governance first, so a governance failure
ends `mise run check` in seconds. It applies to every Nox run: CI's `nox -s quality coverage` skips coverage after
a quality failure, and the proof's matrix gate stops after its first failing interpreter. Each of those fails
either way, and stopping early returns its runner or host slot sooner; what is lost is the report of the sessions
that did not run. Alternative: `--stop-on-first-error` in the `check` task only. Rejected: the time a failing run
spends after its first failure is the same waste in CI and in the proof, and one setting in the noxfile covers all.
The test of this decision imports Nox's private `nox._options` to build Nox's own configuration; a Nox upgrade that
moves it fails that test loudly, which is the signal to adjust it.

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
- **Host spellings that 8.0.0 admitted are refused.** A host whose last label is numeric and that is not four
  decimal octets (`https://127.1/` reached the loopback address), and a name with a trailing dot, now get
  `upstream_route_invalid`; the client writes the address in full or drops the dot. CHANGELOG lists both under
  Changed.
- **More than 32 active origins** close idle connections that a later request would have reused. Each origin
  still keeps up to 8 while it is active, so the cost is one new connection per eviction.

## Migration

Release 8.0.1. It needs no change from AIGW: AIGW writes lower-case hosts without default ports. An installed 8.0.0
listener hands off to 8.0.1, and the payload shape does not change.
