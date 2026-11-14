## Context

`lifecycle/deployment/strategy.py` chose one of three results for a running runtime: `handoff`,
`native_generation` or `unsupported`. `apply._upgrade` carried both transitions. The native-generation one needed
the listener to close its own admission, so `relay/admission.py` held a drain latch with a generation, a deadline
and a lease, `serving/control.py` exposed it at `/control/drain`, and `relay/responses.py` answered
`503 proxy_draining` while it was set.

Both handoff capabilities first shipped in 4.0.6, before the product took its current name at 5.0.0. Releases
5.0.0 to 5.1.12 advertise both. The proposal states why the second transition goes.

## Goals / Non-Goals

**Goals:**

- One transition and one predicate that admits a runtime to it, used by `install`, `rollback` and `reload`.
- No route, state or response that closes admission.
- The published predecessor still hands off to this release, and this release back to it.

**Non-Goals:**

- A change to handoff protocol 2 or to the handed-off listener's own exit lease.
- The readers of an installation without a generation selector.

## Decisions

### One predicate admits a runtime to a transition

`strategy.runtime_supports_handoff` requires a valid identity and both capabilities. `install` and `rollback`
refuse any other runtime with the existing incompatible-runtime message, and `reload` with its existing refusal.
`reload` used a weaker check, the selected-generation capability alone, for the same decision; that check is gone.

Alternative: keep the three-way result and fail on `native_generation`. It would keep a name for a transition
that does not exist.

### Protocol 2 keeps its two admission fields as constants

A 5.1.12 controller requires `accepting: true` and `draining: false` of a runtime that it accepts as a successor
or reads in `status`. The runtime identity of this release therefore emits both as constants, beside the protocol
version. `drain_generation` and `drain_lease_remaining_seconds` had no reader outside the module that produced
them, in this release or in 5.1.12, and leave health.

This release keeps the same two expectations of another runtime. A 5.1.12 listener can still be drained through
its own route, and such a listener is not valid for a transition.

### A failed handoff is rolled back on read-only proof

Before its payload rollback, a failed handoff required the captured predecessor to own the listener and to admit
requests, after a `DELETE /control/drain`. The request is gone and the proof stays: `handoff.predecessor_serves`
reads health once. Without that proof the outcome is unknown and the transaction is preserved, as before.

### One owner closes a refused transaction

`install_asset` closed its transaction when `apply.install` refused it before any projection; `apply.rollback`
did not, so a refused rollback left a `prepared` transaction. `apply.closing_prepared` now holds that rule with
its report of a failed cleanup, and both callers use it.

### The former drain path is an ordinary path

`POST` and `DELETE /control/drain` take the relay path and get the response of any path that names no provider
route. No special refusal is kept: a route that is named only to be refused is still a route.

## Risks / Trade-offs

- A 5.1.12 controller that fails a handoff sends `DELETE /control/drain` to the source listener. In an installed
  product that listener is the 5.1.12 one, which still has the route. Only a 5.1.12 executable run by hand
  against a listener of this release meets the removed route; it then preserves its transaction for recovery,
  which is its unknown-outcome path. → Accepted: the installed command is the newest release's.
- A handed-off process that cannot speak protocol 2 as the successor makes a rollback fail instead of falling
  back to replacement. → Accepted: no release under this name lacks it, and the failure leaves the current
  listener serving.
- The journeys from the published predecessor cannot run on a development host without touching its service.
  → They run in the proof and in hosted CI on three platforms before the lane lands.

## Migration Plan

None for an installation of 5.0.0 or later. A script that called `/control/drain` or read the removed health
fields must stop; the changelog names each.
