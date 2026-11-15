## Context

`lifecycle/deployment/strategy.py` chose one of three results for a running runtime: `handoff`,
`native_generation` or `unsupported`. `apply._upgrade` carried both transitions. The native-generation one needed
the listener to close its own admission, so `relay/admission.py` held a drain latch with a generation, a deadline
and a lease, `serving/control.py` exposed it at `/control/drain`, and `relay/responses.py` answered
`503 proxy_draining` while it was set.

Both handoff capabilities have shipped since 3.1.16, before the product took its current name at 5.0.0. Releases
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
reads health once. It also requires that the listener has begun no handoff since its capture, the condition under
which the failure resolver calls a handoff rolled back: an interrupt can leave the resolver while a handoff is
still in flight, and that handoff may yet commit its successor. Without the proof the outcome is unknown and the
transaction is preserved, as before.

### A finished handoff session is at rest once the listener has refused the request

A listener that a handoff created reports that finished session, `finalized` with its transaction, until its next
handoff begins, and beginning one replaces the session for good: a failed handoff leaves it idle. Every listener
is in that state after an upgrade, a reload or a rollback. The resolver and the proof counted only an idle session
as "nothing in flight", so a request that such a listener refused before beginning it was waited out for the
whole handoff deadline, up to the upstream timeout of 900 seconds, and ended as an unknown outcome.

That session, exactly as the controller captured it before its request, says that the request has not begun. It
does not say that the request will not begin: the listener still shows it between reading the request and
beginning the handoff. A test forces that order through the listener's own handler. A rollback there could
overlap a handoff that then commits, and after a `rollback` command the retained generation would serve under a
selector that names the other release.

The resolver and the proof therefore accept that session only when the listener answered the request with an
error status, which says that its handler has finished. `HandoffRefusedError` carries that answer from the
request to both. Without it, after an interrupt or a lost connection, such a listener is waited out for the
handoff deadline and the outcome is unknown, as in 5.1.12; a handoff that begins in that time is followed to
its end. An idle session counts with or without an answer, as before. Any other session, a finalized
one with another transaction among them, is a handoff in flight or finished.

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
- A handoff request that an idle listener has read and not begun when the controller reads its health is
  invisible to the resolver and to the proof, so a rollback can overlap a handoff that begins in that moment.
  → As before, and only for a listener that the supervisor started: one that a handoff created is covered above.
  The listener compares the request with the generation that the selector names before it begins, so a request
  that it compares after the rollback is refused. One that it compared before is not: a forward candidate is
  deleted by then, and the retained generation of a `rollback` can still take over. Closing that window needs the
  listener's part, or an idle session that counts only with an answer; task 12.5 of
  `terminal-product-convergence` owns it.
- An interrupt in the instant before a listener that a handoff created begins the handoff waits out the handoff
  deadline and ends as an unknown outcome, with its transaction preserved for `recover`. → Accepted: it is what
  5.1.12 does for that listener, and the alternative is the overlap above.
- An interrupt whose cleanup of a refused transaction fails surfaces as a lifecycle error that names both, not as
  the interrupt. → Kept from `install_asset`: the failed cleanup is what the operator must read.
- The incompatible-runtime refusal says "before installing this release" on a rollback too. → Kept: the predicate
  does not tell a runtime that lacks a capability from one that is busy, and a refusal that names each belongs
  with the failure work of `terminal-product-convergence`.
- The journeys from the published predecessor cannot run on a development host without touching its service.
  → They run in the proof and in hosted CI on three platforms before the lane lands. A journey starts from the
  newest release below `VERSION`, so the lane carries the release preparation: its journeys start from 5.1.12.

## Migration Plan

None for an installation of 5.0.0 or later. A script that called `/control/drain` or read the removed health
fields must stop; the changelog names each.
