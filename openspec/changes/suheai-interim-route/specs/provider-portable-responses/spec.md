## MODIFIED Requirements

### Requirement: Provider routes admit only exact Responses and model-catalog targets

The listener SHALL expose provider-scoped loopback Responses targets and exact
read-only model-catalog targets for `dmxapi`, `ucloud`, `aihubmix`, and
`suheai`. Each namespace SHALL resolve only exact, lexically normalized
Responses and model catalog paths against the release-owned HTTPS upstream
mapping. A downstream request SHALL NOT supply or override an upstream URL or
host.

#### Scenario: A client selects each governed provider

- **WHEN** a client sends otherwise equivalent Responses traffic to
  `/dmxapi/v1/responses`, `/ucloud/v1/responses`,
  `/aihubmix/v1/responses`, or `/suheai/v1/responses`
- **THEN** the proxy sends it only to the matching DMXAPI, UCloud, AIHubMix,
  or Suhe.AI HTTPS upstream
- **AND** credentials continue to come from that client request.

#### Scenario: A caller uses an unknown namespace

- **WHEN** a request path matches neither a control endpoint nor one of the
  four canonical namespaces
- **THEN** the proxy rejects it locally
- **AND** it performs no remote network request.

#### Scenario: A caller uses a malformed provider target

- **WHEN** a target contains encoded path material, dot segments, duplicate
  separators, a lookalike suffix, a fragment, or an absolute URL
- **THEN** the proxy rejects it locally
- **AND** unrelated endpoints remain unreachable.

#### Scenario: A consumer reads one provider model catalog

- **WHEN** it sends GET to `/dmxapi/v1/models`, `/ucloud/v1/models`,
  `/aihubmix/v1/models`, or `/suheai/v1/models`
- **THEN** the proxy relays exactly one GET to the matching provider
- **AND** it does not activate Responses projection, cooldown, retry, or
  recovery.

#### Scenario: A rejected route carries an unread body

- **WHEN** an unknown route or unsupported method is rejected before its body
  is consumed
- **THEN** the response closes the connection
- **AND** the unread body is not parsed as another request.
