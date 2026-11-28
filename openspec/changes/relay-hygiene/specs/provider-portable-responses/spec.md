## ADDED Requirements

### Requirement: Every upstream origin has one canonical spelling

The proxy SHALL write an upstream authority in the normal form of RFC 3986 sections 6.2.2.1 and 6.2.3: a
lower-case host, a decimal port without leading zeros, no default port for the scheme, and an IPv6 literal in
the form of RFC 5952. It SHALL refuse port 0, zone identifiers, IPvFuture, all-numeric hosts that are not four
decimal octets, and host names that DNS cannot carry. Cooldown keys, pooled connections, log lines and the
forwarded URL SHALL use the canonical origin.

#### Scenario: Two spellings name one upstream

- **WHEN** clients send to `/https/API.Example.com:443/v1/responses` and to `/https/api.example.com/v1/responses`
- **THEN** both requests go to `https://api.example.com/v1/responses`
- **AND** they share one cooldown, one set of pooled connections and one `upstream=` value in log lines.

#### Scenario: A host is written in a form that resolvers read differently

- **WHEN** a target's host is `127.1`, `0x7f.0.0.1` or another all-numeric form that is not four decimal octets
- **THEN** the proxy rejects it locally with `upstream_route_invalid`
- **AND** it performs no remote network request.

#### Scenario: A host name exceeds what DNS carries

- **WHEN** a target's host has an empty label, a label longer than 63 octets, more than 253 octets in all, or a
  trailing dot
- **THEN** the proxy rejects it locally with `upstream_route_invalid`.

### Requirement: Idle upstream connections stay bounded however many origins clients name

The proxy SHALL keep at most 8 idle upstream connections per address and 32 in all. It SHALL close an idle
connection within about one second of it being 30 seconds old, whether or not another request arrives, and SHALL
keep no state for an address that has no idle connection.

#### Scenario: Clients name more origins than the pool holds

- **WHEN** responses from more than 32 connections complete while their connections stay reusable
- **THEN** the pool closes the least recently released connection beyond 32
- **AND** each address still keeps no more than 8.

#### Scenario: An origin falls silent

- **WHEN** no request names an origin for more than 30 seconds after its last response
- **THEN** the listener's serve loop closes that origin's idle connections
- **AND** the pool holds no entry for its address.

### Requirement: Data-plane refusals are structured and named once

A data request that fails the local-client rule SHALL receive HTTP 403 with the local error envelope and the code
`local_client_required`, and the response SHALL close the connection. Each data-plane refusal SHALL use its error
code as its counter, failure class and log event.

#### Scenario: A refused web page learns why

- **WHEN** a data request carries an `Origin` header, a Fetch Metadata header or a non-loopback `Host`
- **THEN** the proxy answers HTTP 403 with the JSON error envelope, type `invalid_request_error` and code
  `local_client_required`, and closes the connection
- **AND** it logs `event=local_client_required` and counts the refusal under that name.

#### Scenario: A route is refused

- **WHEN** the proxy rejects a target with `upstream_route_invalid` or a method with
  `upstream_route_method_not_allowed`
- **THEN** its counter, failure class and log event carry that same code.

### Requirement: A relayed response carries one Server and one Date

A response that the proxy relays SHALL carry the upstream's `Server` and `Date` fields once each and no field of
the proxy's in their place. The proxy SHALL add its own `Date` only when the upstream sent none. A response that
the proxy makes itself SHALL carry the proxy's `Server` and `Date`.

#### Scenario: An upstream answers with its own fields

- **WHEN** an upstream's JSON, error or streamed response carries `Server` and `Date`
- **THEN** the client receives exactly one `Server` and one `Date`, both the upstream's.

#### Scenario: An upstream sends no Date

- **WHEN** a relayed response has no `Date` field
- **THEN** the client receives exactly one `Date`, written by the proxy.
