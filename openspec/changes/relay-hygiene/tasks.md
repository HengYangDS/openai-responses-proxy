## 1. Canonical origins

- [x] 1.1 Add tests to `tests/relay/test_addressing.py`: case, a default port, a zero-padded port and an IPv6 literal
      each resolve to the one canonical origin and URL; port 0, a zone identifier, IPvFuture, non-dotted numeric
      hosts, a label over 63 octets, a host over 253 octets, an empty label and a trailing dot are refused; `http`
      to `LOCALHOST` is loopback. Done when they fail before 1.2 and pass after it.
- [x] 1.2 Canonicalize the authority in `relay/addressing.resolve` (D1). Done when 1.1's tests and the relay tests
      pass.

## 2. Bounded idle connections

- [x] 2.1 Add tests to `tests/relay/test_connection_reuse.py`: releases beyond 32 in all close the least recently released
      connection; `Pool.expire` closes connections idle for longer than 30 seconds and leaves no empty address; and
      the listener's `service_actions` expires the pool. Done when they fail before 2.2 and pass after it.
- [x] 2.2 Bound the pool and call its expiry from `ResilientProxyServer.service_actions` (D2). Done when 2.1's tests
      and the relay and serving tests pass.

## 3. Refusals and header fields

- [x] 3.1 Add tests: a data request from a client that is not local gets a 403 JSON envelope with
      `local_client_required`, closes the connection, logs that event and counts it; route refusals count and log
      `upstream_route_invalid` and `upstream_route_method_not_allowed`; a relayed error, stream and model catalog
      carry exactly one `Server` and one `Date`, the upstream's, and the proxy's `Date` when the upstream sent none;
      a validated JSON response carries the proxy's own. Done when they fail before 3.2 and pass after it.
- [x] 3.2 Implement D3, D4 and D5. Done when 3.1's tests and the relay and serving tests pass.

## 4. Small corrections and workflow

- [x] 4.1 Write the agent-delivery test's expected body out literally, and correct the
      `verify_payload_manifest` docstring (D8). Done when the test fails against a projection that changes the
      collaboration catalog and passes on the product.
- [x] 4.2 Set Nox's `stop_on_first_error` (D6), with a test that a failing governance session ends `full` before
      the quality session. Done when the test passes.
- [x] 4.3 Add the rule of D7 to CONTRIBUTING, and update README, the architecture notes and the capability
      documentation where they describe origins, the pool or refusals. Done when governance and the prose gate pass.

## 5. Release preparation

- [ ] 5.1 Set `VERSION` to 8.0.1 and record the changes under `## [8.0.1]` in `CHANGELOG.md`. Done when the release
      metadata check passes.
- [ ] 5.2 Answer one fresh independent review of the whole Change in code or design. Done when every finding has an
      answer.
- [ ] 5.3 Pass the ETHOS full proof under a host ticket and land on both Forges' `dev`. Done when both `dev` branches
      hold the head and their pipelines are green, `Published release compatibility` included.
