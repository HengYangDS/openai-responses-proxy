## Why

An upgrade, a rollback and a reload replace the running listener by a handoff that a controller requests over
the loopback control route. When the request fails, the controller restores the previous payload selection
only after it has read from health that the listener is at rest. Release 6.0.0 closed one hole in that
reading: a listener that an earlier handoff created counts as at rest only once it has answered the request.
For a listener that the supervisor started, whose handoff session is idle, the hole stayed open; the archived
design of `single-handoff-transition` names it, and task 12.5 of `terminal-product-convergence` owns it.

An idle session is what a listener shows before it reads a request, while it compares a request that it has
read, and after a handoff that it rolled back. So a controller whose request ended without an answer reads
idle and restores the selection, and the listener then begins the handoff and commits a successor that the
selection no longer names. After a `rollback` command the older release then serves while the selector and
the installed record name the newer one, no transaction remains, and `status` reports `degraded`. A test
forces that order through the listener's own handler: at 6.0.1 the controller answers "rolled back" while the
handler goes on to begin. `recover` decides the same way on one read of health.

## What Changes

- After a handoff request has failed, a listener at rest proves that no handoff of that request is to come
  only together with evidence of the listener's part in it: the request was never sent, the listener refused
  it, the listener acknowledged it, its handoff was seen to begin and end, or the time in which the listener
  may begin it has run out.
- The controller ends the connection of its handoff request with a reset, so that a request which it has
  given up reaches a listener only on a connection that the listener already has. A measurement on Linux
  showed the other case: a listener whose queue was full took a connection later, behind connections that
  were made after it, and read the request of a client that had closed in the ordinary way.
- A listener begins a handoff only within the request's own timeout, counted from the moment at which it
  accepted the request's connection, and refuses the request afterwards with HTTP 408 and the error
  `handoff_request_expired`.
- A request that ends without the listener's answer, against a listener at rest, is no longer rolled back at
  once. The command waits for the request's timeout and one second, 31 seconds by default, and rolls back if
  the listener is still at rest then; a handoff that begins in that time is followed to its end.
- `recover` waits for the longest time that a request can carry and one second, 121 seconds, before it
  restores the prior selection of a transaction whose handoff may have been requested, and decides on what
  the listener shows after that time. In its human form it says on standard error that it waits.
- `recover` restores nothing over a predecessor that has handed the transaction off or has a handoff in
  flight, and finalizes no successor that its predecessor can still abort. Before, it read both as settled.
- Unchanged: a refused request is rolled back at once; a request that was never sent is rolled back at once;
  handoff protocol 2 keeps its fields and routes; the journal keeps its form.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `runtime-upgrade`: a failed handoff request is settled by evidence of the listener's part in it, and the
  listener bounds the time in which it begins a handoff.

## Impact

Code: `lifecycle/deployment/handoff` (the evidence and the rule), `runtime/loopback` (the request's
connection), `serving/server`, `serving/control` and `serving/handoff/transaction` (the bound and its
refusal), `lifecycle/control`, `lifecycle/transaction/recovery` and `lifecycle/uninstall` (`recover`), `cli`
(the line that `recover` writes while it waits), and their tests. `docs/evidence` gains the measurement. No
command, option, route, field of `status --json` or exit code changes.

- **Authority boundary:** the controller reads the listener's health and sends the one handoff request; it
  sends the listener nothing else, and it never ends a listener to settle an outcome.
- **Evidence:** unit tests force each order through the listener's own handler and session, with no thread,
  process or real clock deciding it. Tests in `tests/serving/test_accept.py` and
  `tests/runtime/test_loopback_reset.py` use real loopback sockets, without a thread, for what the operating
  system does: the order of a queue, the stamp of an accept, and the reset. The local proof and the
  hosted graph run all of them on macOS, Linux and Windows. What a full queue does was measured on macOS and
  on Linux and is recorded with its scripts in `docs/evidence/listen-queue-measurement.md`. The journeys from
  the published 6.0.1 cover an upgrade from a listener without the bound and the rollback to it. Acceptance
  on the operator host is the installed upgrade.
- **Not addressed:** the upgrade to this release itself, which 6.0.1's command requests of 6.0.1's listener.
  A listener of 6.0.1 or earlier does not bound its begin, so after a rollback to it the wait is not enforced
  by the listener. What a full queue does on Windows was not measured. The readers of an installation without a generation
  selector stay with task 5.8 of `terminal-product-convergence`.
- **Non-goals:** a deadline or an identifier of a request on the wire, a route that withdraws a request, and a
  record of the request in the journal. The design says why each is not needed.
