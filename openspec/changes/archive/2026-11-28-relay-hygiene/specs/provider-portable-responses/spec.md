## MODIFIED Requirements

### Requirement: Upstream addressing admits only exact Responses and model-catalog targets

The listener SHALL accept a data request only at
`/<scheme>/<authority>/<segment>*/<resource>`, where the resource is `responses`
or `models`, and SHALL forward it, with its query, to
`<scheme>://<canonical authority>/<segment>*/<resource>`. The scheme SHALL be
`https`, or `http` for a loopback authority only. The authority SHALL carry no
user information, and the quoted path SHALL equal the path. A data request
SHALL carry a loopback `Host`, no `Origin` and no `Sec-Fetch-*` header.

#### Scenario: A client names an HTTPS upstream

- **WHEN** a client sends Responses traffic to `/https/api.example.com/v1/responses`
- **THEN** the proxy sends it only to `https://api.example.com/v1/responses`
- **AND** credentials continue to come from that client request.

#### Scenario: A consumer reads a model catalog

- **WHEN** it sends GET to `/https/api.example.com/v1/models?client_version=1`
- **THEN** the proxy relays exactly one GET to
  `https://api.example.com/v1/models?client_version=1`
- **AND** it does not activate Responses projection, cooldown, retry, or
  recovery.

#### Scenario: A caller uses a malformed upstream target

- **WHEN** a target contains encoded path material, a dot or empty segment,
  user information, a scheme other than lower-case `https` or `http`, a
  fragment, an absolute URL, or a resource other than `responses` or `models`
- **THEN** the proxy rejects it locally with `upstream_route_invalid`
- **AND** it performs no remote network request.

#### Scenario: Plain HTTP names a remote host

- **WHEN** a target's scheme is `http` and its authority is not a loopback
  address
- **THEN** the proxy rejects it locally with `upstream_route_invalid`.

#### Scenario: A web page sends a data request

- **WHEN** a data request carries an `Origin` header, a Fetch Metadata header or a non-loopback `Host`
- **THEN** the proxy answers HTTP 403 with the JSON error envelope, type `invalid_request_error` and code
  `local_client_required`, before resolving its path, and closes the connection
- **AND** it performs no remote network request.

#### Scenario: A rejected route carries an unread body

- **WHEN** an invalid target, an unsupported method or a client that is not local is refused before the request
  body is consumed
- **THEN** the response closes the connection
- **AND** the unread body is not parsed as another request.

## ADDED Requirements

### Requirement: Every upstream origin has one canonical spelling

The canonical authority SHALL be the normal form of RFC 3986 sections 6.2.2.1
and 6.2.3: a lower-case host, a decimal port without leading zeros and without
an empty or default port, and an RFC 5952 IPv6 literal. The proxy SHALL refuse
port 0, zone identifiers, IPvFuture, a host whose last label is numeric unless
it is four decimal octets, and a host that is not one fully qualified DNS name.
Cooldown keys, pooled connections and log lines SHALL use the canonical origin.

#### Scenario: Two spellings name one upstream

- **WHEN** clients send to `/https/API.Example.com:443/v1/responses` and to `/https/api.example.com/v1/responses`
- **THEN** both requests go to `https://api.example.com/v1/responses`
- **AND** they share one cooldown, one set of pooled connections and one `upstream=` value in log lines.

#### Scenario: A host is written in a form that resolvers read differently

- **WHEN** a target's host is `127.1`, `0x7f.0.0.1`, `api.example.123` or another host whose last label is numeric and
  that is not four decimal octets
- **THEN** the proxy rejects it locally with `upstream_route_invalid`
- **AND** it performs no remote network request.

#### Scenario: A host is not one fully qualified DNS name

- **WHEN** a target's host has an empty label, a label longer than 63 octets, more than 253 octets in all, or a
  trailing dot
- **THEN** the proxy rejects it locally with `upstream_route_invalid`.

### Requirement: Idle upstream connections stay bounded however many origins clients name

The proxy SHALL keep at most 8 idle upstream connections per address and 32 in all. It SHALL close an idle
connection within about one second of it being 30 seconds old, whether or not another request arrives, and SHALL
keep no state for an address that has no idle connection. A failure to close one connection SHALL NOT stop the
listener or the closing of the others.

#### Scenario: Clients name more origins than the pool holds

- **WHEN** responses from more than 32 connections complete while their connections stay reusable
- **THEN** the pool closes the least recently released connection beyond 32, whichever address holds it
- **AND** each address still keeps no more than 8.

#### Scenario: An origin falls silent

- **WHEN** no request names an origin for more than 30 seconds after its last response
- **THEN** the listener's serve loop closes that origin's idle connections
- **AND** the pool holds no entry for its address.

### Requirement: Local refusals are named by their error code

The proxy SHALL count each refusal of a client that is not local, of an invalid target and of an unsupported method
under its error code: `local_client_required`, `upstream_route_invalid` and `upstream_route_method_not_allowed`. A
route refusal SHALL also use that code as its failure class and log event.

#### Scenario: A route is refused

- **WHEN** the proxy rejects a target with `upstream_route_invalid` or a method with
  `upstream_route_method_not_allowed`
- **THEN** its counter, failure class and log event carry that same code.

### Requirement: A refusal that any web page can cause stays quiet

Because any local web page can cause the refusal of a client that is not local, that refusal SHALL NOT replace the
last failure, and the proxy SHALL write at most one log line for it per minute, naming the number refused since
the previous line.

#### Scenario: A web page repeats a refused request

- **WHEN** clients that are not local send several data requests within one minute
- **THEN** each is counted under `local_client_required`
- **AND** one log line names the refusal, the next names the number refused since it, and the last failure
  that `status --json` reports is unchanged.

### Requirement: A relayed response carries one Server and one Date

A response that passes on the upstream's header fields (an upstream error, a stream or a model catalog) SHALL carry
the upstream's `Server` and `Date` fields once each and no field of the proxy's in their place. The proxy SHALL add
its own `Date` only when the upstream sent none. A response that the proxy writes itself, a validated non-stream
Responses body included, SHALL carry the proxy's `Server` and `Date` once each.

#### Scenario: An upstream answers with its own fields

- **WHEN** an upstream's error, streamed or model-catalog response carries `Server` and `Date`
- **THEN** the client receives exactly one `Server` and one `Date`, both the upstream's.

#### Scenario: An upstream sends no Date

- **WHEN** a relayed response has no `Date` field
- **THEN** the client receives exactly one `Date`, written by the proxy.

#### Scenario: The proxy writes a validated body

- **WHEN** the proxy returns a non-stream Responses body that it has validated
- **THEN** the client receives exactly one `Server` and one `Date`, both the proxy's.

### Requirement: A field that belongs to one connection stays on it

In both directions the proxy SHALL pass on no field that belongs to one connection: `Connection`, every field that
`Connection` names, `Proxy-Connection`, `Keep-Alive`, `Transfer-Encoding`, `Upgrade`, `Trailer`, and the field of
RFC 9110 section 10.1.4 that names the transfer codings a client accepts.

#### Scenario: A field belongs to one connection

- **WHEN** a request or a relayed response names a field in `Connection`, or carries `Trailer`
- **THEN** that field does not reach the other side.

### Requirement: A relayed response matches the upstream response it delivers

A relayed stream SHALL carry the header fields of the upstream response whose content it delivers, and a relayed
204 or 304 SHALL carry no body and no chunked framing.

#### Scenario: A stream is reopened before content

- **WHEN** an upstream stream fails before content and the proxy delivers a reopened stream
- **THEN** the client receives the header fields of the reopened stream, not those of the failed one.

#### Scenario: A model catalog is not modified

- **WHEN** an upstream answers a model-catalog request with HTTP 204 or 304
- **THEN** the client receives that status with no body and no chunked framing.
