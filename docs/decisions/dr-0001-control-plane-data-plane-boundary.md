---
subject: openai-responses-proxy:decision-0001-control-plane-data-plane-boundary
role: decision
decision_id: DR-0001
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0001: Keep Control Plane Separate from Proxy Data Plane

- Date: 2026-07-14
- Last amended: 2026-08-16

## Context

A client control plane projects provider configuration. The proxy provides
local Responses compatibility and a loopback service. Letting both write the
same configuration or manage each other's lifecycle causes drift, unsafe
recovery, and ambiguous rollback responsibility.

## Decision

The client control plane owns provider blocks, credentials, endpoint selection,
and configuration projection. OpenAI Responses Proxy owns outbound protocol
normalization, its executable payload, and its native service lifecycle. The
installed proxy is generated from source and verified with a manifest. It never
writes client configuration or invokes a particular control-plane product.

## Alternatives Rejected

- **General multi-provider gateway:** rejected because routing, fallback,
  billing, and organization policy belong outside this compatibility edge.
- **Proxy-owned client configuration:** rejected because it creates two writers
  for endpoint and credential state.
- **Control-plane-managed proxy lifecycle:** rejected because independently
  useful products should not share one failure and release domain.

## Consequences and Boundary

The products evolve independently and each mutation has one owner. Runtime
repair rebuilds the proxy projection; route repair remains a client control-plane
operation. Neither path permits session-history mutation.

The proxy is intentionally complementary to, rather than a smaller clone of,
general gateways. Its durable advantage is a closed portable grammar, bounded
recovery, and a native lifecycle with no client-state authority.

## Evidence and Revisit

The [authority and runtime boundary](../architecture/authority-and-runtime-boundary.md)
explains the independent owners; the [product interface](../../openspec/specs/product-interface/spec.md)
owns their observable contract.

Revisit only if the proxy becomes an explicit client configuration control
plane or a client control plane adopts proxy transport and native service
lifecycle ownership.
