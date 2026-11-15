## Why

An upgrade or a rollback replaces the running listener in one of two ways. A runtime that advertises both handoff
capabilities hands its listener to the successor and keeps admitting requests. Any other verified runtime is
drained and replaced: the controller closes admission through `/control/drain`, waits, ends the process and lets
native supervision start the successor.

Every release under this product's name, from 5.0.0 on, advertises both capabilities, so nothing reaches the
second way. It still costs a control route that closes admission, a local `503`, an admission latch with a lease,
and a second strategy that each transition has to choose from. The main `runtime-upgrade` specification even
names it as the way every rollback works, which the product has not done since handoff preserved admission.

## What Changes

- **BREAKING**: a lifecycle transition hands off or is refused. A verified runtime that lacks a handoff capability
  is refused before any change, with "installed runtime is incompatible; remove it before installing this
  release". `reload` follows the same rule; it accepted the selected-generation capability alone.
- **BREAKING**: `POST` and `DELETE /control/drain` are no longer control routes. The local response
  `503 proxy_draining`, the counters `responses_rejected_while_draining` and `drain_leases_expired`, and the
  health fields `drain_generation` and `drain_lease_remaining_seconds` are gone. Health keeps `accepting: true`
  and `draining: false`, which handoff protocol 2 names.
- A handoff that fails before its successor commits is rolled back only with proof, read from health, that the
  captured predecessor still serves. Before, the controller first sent a request to reopen admission.
- A rollback that is refused before any projection closes the transaction that it opened. Before, it left a
  prepared transaction behind, and every later command asked for `recover`.
- A handoff request that a listener refuses with an answer before beginning it is resolved at once, also for a
  listener that a handoff created. Before, such a listener was waited out for the whole handoff deadline, and
  the outcome was unknown; without the listener's answer it still is.
- The main `runtime-upgrade` statement that rollback drains the listener is corrected: rollback hands off as an
  upgrade does.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `runtime-upgrade`: one transition, the handoff; no admission drain; a refused rollback leaves no transaction.
- `provider-portable-responses`: a request checks its provider's cooldown before remote I/O, and no lifecycle
  drain barrier.

## Impact

Code: `lifecycle/deployment` (`apply`, `handoff`, `strategy`), `lifecycle/install`, `relay/admission`,
`relay/responses`, `relay/telemetry`, `serving/control`, `serving/server`, `serving/entrypoint`,
`serving/handoff/transaction`, and their tests.

Handoff protocol 2 does not change. An upgrade from 5.1.12 and a rollback to it hand off as before.

Evidence: the unit tests and the local proof cover the refusals and the failure paths. The journeys from the
published 5.1.12 on macOS, Linux and Windows, which run in the proof and in hosted CI, cover the upgrade and the
rollback to it under load. Acceptance on the operator host is the installed upgrade and the rollback.

Not addressed: the readers of an installation without a generation selector, which task 5.8 of
`terminal-product-convergence` still owns.
