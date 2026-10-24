---
subject: openai-responses-proxy:decision-0006-semantic-fit-before-framework-adoption
role: decision
decision_id: DR-0006
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0006: Require Semantic Fit Before Framework Adoption

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

## Alternatives Rejected

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

## Consequences and Boundary

- The inbound server stays small and synchronous while that remains the least
  complex implementation of the inherited-listener contract.
- The private socket traversal remains an explicit design liability, not a
  reason to accept a replacement that moves the same liability or adds a second
  lifecycle.
- A framework is not rejected because it is external or accepted because it is
  modern; total owned complexity and product fit decide.
- Dependency versions and hashes remain supply-chain projections of the lock,
  not literals copied into CI or documentation.

## Evidence and Revisit

The [provider-portable Responses](../../openspec/specs/provider-portable-responses/spec.md)
and [runtime upgrade](../../openspec/specs/runtime-upgrade/spec.md) contracts
define the behavior a replacement must preserve. The rejected timeout behavior
is specific to HTTPX 0.28.1, not a claim about all later releases.

Revisit the inbound decision if an ASGI server proves inherited-socket handoff,
raw stream parity, smaller frozen assets, and less owned lifecycle code. Revisit
the upstream decision when a stable candidate exposes public per-read deadline
control for an active synchronous stream and can prove raw-byte, recovery,
direct-routing, frozen-release, and three-platform parity while deleting more
transport-specific complexity than it introduces.

**Amendment, 2026-10-22: gateway and transport review.**

Retain the current transport for this convergence. Do not embed a general
multi-provider gateway. HTTPCore's public network backend is a valid replacement
candidate, but adoption requires demonstrated net simplification and the existing
release acceptance; it is not rejected as impossible.

The comparison separates the portable Responses policy and native lifecycle
from generic HTTP mechanics. The current parser is Python's `http.client`, not
an independently maintained HTTP grammar. Product code owns deadline-aware I/O,
raw-body admission and response capabilities. A replacement must remove those
liabilities without introducing another policy or runtime owner.

| Candidate and primary source                                                              | Replacement value                                                                                            | Decision and cost                                                                                                                                                                                                                                     |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [LiteLLM 1.106.2](https://github.com/BerriAI/litellm/blob/v1.106.2/pyproject.toml)        | Provider translation, SDK and gateway routing, including Responses.                                          | Do not embed. Its SDK adds a Rust build backend and a broad model/validation stack; `filelock<4` conflicts with this product's lock. Gateway extras add a server and policy domain without proving portable replay or native listener succession.     |
| [Portkey Gateway 1.15.2](https://github.com/Portkey-AI/gateway/blob/v1.15.2/package.json) | Local Node gateway with retries, routing and fallbacks.                                                      | Do not embed. It introduces a Node/Hono service and install hooks (`prepare`, `postinstall`) while the existing product must still own its native lifecycle and portable stream semantics. An independently selected upstream remains possible.       |
| [HTTPX 0.28.1](https://github.com/encode/httpx/blob/0.28.1/docs/advanced/timeouts.md)     | Public raw-byte iteration, direct routing and connection pooling.                                            | Do not adopt. Its read timeout is a per-chunk inactivity limit, not an absolute exchange deadline. The earlier active-stream probe remains applicable to this unchanged stable version.                                                               |
| [HTTPCore 1.0.9](https://github.com/encode/httpcore/blob/1.0.9/docs/network-backends.md)  | A public network backend can clamp each read, write and TLS operation; the pool owns HTTP parsing and reuse. | Retain as a candidate. It still requires deadline propagation across reused connections, backend and pool shutdown, response adaptation and native qualification. No smaller accepted implementation or measured total benefit has been demonstrated. |
| [h11 0.16.0](https://github.com/python-hyper/h11/blob/v0.16.0/README.rst)                 | A standalone HTTP/1.1 state machine with no I/O.                                                             | Do not add alone. The standard library already owns parsing; replacing it with h11 leaves DNS, TLS, deadlines and resource disposal with this product and requires new glue.                                                                          |

The official registries reported these stable versions on the review date.
[HTTPX's raw API](https://github.com/encode/httpx/blob/0.28.1/docs/quickstart.md)
does preserve encoded bytes. Its limitation is deadline control, not raw-byte
support. [HTTPCore's HTTP/1.1 body iterator](https://github.com/encode/httpcore/blob/1.0.9/httpcore/_sync/http11.py)
captures the read timeout when iteration starts, while the documented network
backend permits custom per-operation control. Using that supported extension
would be an integration choice, not a vendor patch.

This decision does not certify the current transport as complete. Native
`getaddrinfo` is synchronous and is not interrupted by the surrounding deadline
checks. Upstream connection reuse and final performance acceptance are also
unproved. These obligations remain with the existing transport acceptance task;
retaining a library must not turn a known gap into a guarantee.

Reopen the replacement choice when a bounded public-backend implementation
removes private response traversal and demonstrates the same total deadline,
raw bytes, direct TLS routing, retry ownership and resource cleanup. Compare its
maintained code and frozen assets with the current path, including the added
pool and dependency lifetime. No new dependency, resolver execution, benchmark
claim or three-platform replacement proof is implied by this review.

**Amendment, 2026-10-25: resolution and downstream writes.**

The exchange deadline now bounds native address resolution and every write to
the client. Native `getaddrinfo` still cannot be interrupted, so lookups run on
at most four daemon resolver threads owned by the upstream transport, and an
exchange waits for its lookup only until its deadline. Exchanges that need the
same address share one lookup; numeric addresses need no lookup. A lookup that
outlives its waiters ends on its thread without holding an exchange, upstream
connection or admission slot. The fixed set of threads is the bound: no thread
starts per request and none is left behind per expired wait.

A per-request resolver thread was rejected because stalled lookups would
accumulate threads. A non-native resolver library was rejected because it would
bypass the operating system's hosts file, scoped and split DNS, and resolver
policy. Caching addresses was not adopted: the operating system already caches
them, and a second cache would add an expiry policy that this product does not
own.

Every downstream status, header, event and terminator write spends the
exchange's remaining budget. A client that stops reading is cut off at the
deadline, which releases the upstream connection and the admission slot. A
structured failure that starts after the deadline gets one second instead of an
unbounded write.

Connection reuse remains with the transport acceptance task. This amendment
admits no dependency and no alternate HTTP parser.
