---
subject: openai-responses-proxy:decision-0006-semantic-fit-before-framework-adoption
role: decision
state: canonical
relations:
    part_of: README.md
---

# DR-0006: Require Semantic Fit Before Framework Adoption

- Status: accepted
- Date: 2026-08-22

## Context

The product currently uses the Python standard-library HTTP server for its
loopback ingress and `urllib` for upstream traffic. Mature frameworks can remove
incidental protocol, timeout, validation, and test infrastructure, but a broad
framework can also add a second lifecycle, configuration surface, dependency
graph, and release burden without replacing the product's difficult behavior.

The difficult boundaries are byte-preserving Responses and SSE relay, inherited
listener transfer during a rolling native handoff, bounded drain and recovery,
and one frozen executable on macOS, Linux, and Windows. OpenAPI generation,
general application routing, browser sessions, and public web deployment are
not product responsibilities.

## Decision

Dependencies are admitted by semantic replacement, not popularity. A candidate
must delete or substantially simplify an owned boundary, preserve the exact
product contracts, work in the frozen native distribution, and reduce total
development, verification, and maintenance cost.

FastAPI is not adopted for the loopback ingress. Its primary value—typed API
modeling, validation, OpenAPI, and application routing—does not replace the
native supervision or listener-handoff boundary. Using it would also introduce
an ASGI server and data-model stack while the product must still own raw stream
framing, drain admission, inherited-socket lifecycle, and exact process
identity.

HTTPX 0.28.1 is not selected for the upstream transport. Its public timeout
configuration is fixed when an active HTTP/1.1 response-body iterator begins.
Changing the request's read-timeout extension after the first raw chunk does not
shorten the next blocked read, so HTTPX cannot preserve the existing total SSE
deadline without private HTTPCore access or an additional cancellation owner.

The current `urllib` transport remains the sole implementation until a candidate
proves public dynamic read-budget control, raw encoded bytes, direct proxy
isolation, provider-scoped recovery, frozen native portability, and net deletion
of owned complexity. No speculative dependency or parallel fallback is admitted.

## Consequences

- The inbound server stays small and synchronous while that remains the least
  complex implementation of the inherited-listener contract.
- The private socket traversal remains an explicit design liability, not a
  reason to accept a replacement that moves the same liability or adds a second
  lifecycle.
- A framework is not rejected because it is external or accepted because it is
  modern; total owned complexity and product fit decide.
- Dependency versions and hashes remain supply-chain projections of the lock,
  not literals copied into CI or documentation.

## Alternatives Considered

- **FastAPI plus Uvicorn for all HTTP behavior:** rejected because it does not
  own native service installation, rolling listener transfer, or byte-level
  relay semantics and would widen the runtime surface before deleting them.
- **Adopt HTTPX 0.28.1 for upstream traffic:** rejected because its public sync
  streaming API cannot update the active read deadline after iteration begins;
  private HTTPCore traversal would reproduce the defect under another package.
- **Add a reader thread or async cancellation layer around HTTPX:** rejected
  because it adds ownership, shutdown, and failure states without reducing the
  total maintained surface.
- **Keep `urllib` without a replacement criterion:** rejected; the current path
  is retained only while it is the smallest proven implementation, and its
  private timeout traversal remains a named revisit trigger.
- **Rewrite the entire service as asynchronous code:** rejected as an
  unbounded migration without evidence that concurrency, rather than upstream
  latency and provider behavior, is the limiting product risk.

## Revisit Trigger

Revisit the inbound decision if an ASGI server proves inherited-socket handoff,
raw stream parity, smaller frozen assets, and less owned lifecycle code. Revisit
the upstream decision when a stable candidate exposes public per-read deadline
control for an active synchronous stream and can prove raw-byte, recovery,
direct-routing, frozen-release, and three-platform parity while deleting more
transport-specific complexity than it introduces.
