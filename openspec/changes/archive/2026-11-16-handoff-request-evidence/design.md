## Context

See [proposal.md](proposal.md). The controller's side is `lifecycle/deployment/handoff.py`: `request` sends
the one handoff request, `resolve_after_controller_failure` polls health after a failure, and
`predecessor_serves` is the proof before the payload is restored. The listener's side is `serving/control.py`
(the handler of the request) and `serving/handoff/transaction.py` (the handoff session, guarded by one lock).
`recover` is `lifecycle/control.py` with `lifecycle/transaction/recovery.py`.

A listener's handoff session is `idle`, in flight (`preparing` to `committing`, with the request's
transaction), or `finalized` with the transaction of the handoff that created the listener. "At rest" below
means idle, or that finalized session exactly as the controller captured it before its request.

## Goals / Non-Goals

**Goals:**

- No order of a controller's failure and a listener's steps restores a selection that a later commit of that
  listener contradicts.
- A request that failed without an answer ends in a bounded time with a rollback or a followed handoff, not
  with an unknown outcome, wherever the listener is of this release.
- `recover` reaches the same decisions from what it can read after the controller is gone.

**Non-Goals:**

- A change to the wire of handoff protocol 2, to the journal's form, or to a route.
- A listener of 6.0.1 or earlier: it does not bound its begin, and no later controller can make it.

## Decisions

### A listener at rest settles a failed request only with evidence

- **Rests on:** `tests/lifecycle/deployment/handoff/test_window.py`, which runs the listener's own handler up
  to its call of `prepare`, lets the controller fail and resolve there, and then lets the handler begin. At
  `6c4ff8bb` the resolver answers `rolled_back` and the proof `True`. The cases of what ends a request, each
  with the place in `request` that produces it:

  | What ended the request                                     | Evidence     | At rest when the listener shows |
  | ---------------------------------------------------------- | ------------ | ------------------------------- |
  | A failure before the first byte of the request is written  | unsent       | idle, or the captured session   |
  | An error status from the listener                          | refused      | idle, or the captured session   |
  | Any failure after a complete READY                         | acknowledged | idle                            |
  | No answer, then the request's transaction in the session   | observed     | idle                            |
  | No answer, then the request's timeout passed at rest       | expired      | idle, or the captured session   |
  | Anything else: a transport error, a reply that is no READY | none         | never                           |

  An acknowledged or observed handoff replaces the session for good, so the captured session cannot follow
  it; a rolled-back handoff leaves the session idle.

- **Considered:** keep an idle session as at rest and close the listener's window alone; let the request
  return a record that the caller passes on; wrap every failure in one type that carries the evidence; record
  the evidence on the failure that `request` raises.
- **Chosen because:** the listener cannot close the window alone: a request that it has not read has no trace
  in it, and a 6.0.1 listener stays as it is. A record that the caller passes on needs a state that tells a
  fresh record from a used one to stay sound, and three callers thread it. A wrapping type changes what an
  interrupt is for every caller above. The failure already carries the refusal by its type since 6.0.0; the
  evidence is one enumeration beside it, written where it is learnt and read by one function,
  `request_evidence`. A second failure, such as a second interrupt in the resolver, carries nothing, and the
  transaction is preserved.
- **Applies to:** upgrade, rollback and reload, in the resolver and in the proof alike. A failure that fits no
  row holds no evidence.

### The listener bounds its begin from the accept of the request's connection

- **Rests on:** a listener accepts the connections that are in its queue in the order in which they entered
  it. POSIX states it for `accept` ("the first connection on the queue of pending connections"), the
  listen-queue measurement (`docs/evidence/listen-queue-measurement.md`) found it on macOS and on
  Linux, and `tests/serving/test_accept.py`, which the hosted graph runs on three platforms, connects eight
  loopback clients to a listener that nobody accepts from and takes them out in that order. The order of
  entering the queue is not always the order of connecting; the next decision is about that. The standard
  library's server loop accepts one connection, hands it to a thread and only then accepts the next
  (`socketserver.BaseServer.serve_forever`, read in CPython 3.12 to 3.14), and the product starts one such
  loop at a time in a process (`serving/handoff/transaction.py`, read; no test holds it).
  `ResilientProxyServer.get_request` stamps a connection before that loop accepts the next, so a connection
  that is accepted later is stamped no earlier, however long the accepting thread waits for a processor. The
  listener and the controller each measure a duration on their own monotonic clock; nothing compares the two
  clocks. The session's lock guards both the begin and the read of the session for health:
  `tests/serving/handoff/test_bound.py` holds that the clock is read under the lock and after any wait for
  it, that a begin shows its session before it releases the lock, and that a health read reads the session
  under that lock.
- **Considered:** a bound from the moment at which the handler has read the request; a deadline that the
  request carries; an identifier that the request carries and a route that withdraws it; a ticket that the
  listener issues before the request; the state of the controller's connection at the begin.
- **Chosen because:** a bound from the read leaves a request that is accepted and not yet read without any
  limit. A carried deadline needs one clock that both processes read alike, and the wall clock steps. A
  withdrawal is exact, and costs a field, a route, a list in the listener and an identifier of its own,
  because a rollback's request names the retained generation and two rollbacks share it. A ticket makes the
  handoff two requests and the wire incompatible with 6.0.1, which a patch release must not be. The state of
  the connection rests on when a close reaches its peer, which no platform states. The accept stamp needs
  none of these: every connection that the listener accepts after the request's connection is stamped later,
  so a health read that is answered marks a time after which the request's own timeout bounds its begin.
- **Applies to:** every handoff request. A request on a connection that was accepted earlier than its timeout
  ago is refused; the controller opens a connection for each request.

### The controller ends its request's connection with a reset

- **Rests on:** the listen-queue measurement (`docs/evidence/listen-queue-measurement.md`). On Linux a
  client can hold a finished connect that the listener's full queue did not take. The listener takes that
  connection later, after connections that were made later, and reads a request that the client sent before
  it closed in the ordinary way (9 of 10 clients). A client that closed with a reset was never accepted (0 of
  8). On macOS a full queue resets the connect or the connection right after it, and nothing arrives later. A
  connection that is already in the queue is accepted with its request after a reset as well, on both
  hosts. `tests/runtime/test_loopback_reset.py` holds on every platform of the hosted graph that a
  connection which its sender gave up, answered or left by ending its process ends with a reset and never
  with an ordinary end of stream. Windows was not measured.
- **Considered:** leave the ordinary close and name the full queue as a risk; make the controller's wait as
  long as the longest retransmission of a handshake; close the request's connection with a reset.
- **Chosen because:** with an ordinary close the request of a controller that gave up can reach the listener
  after the controller's wait has ended, which is the defect that this Change closes; a first version of this
  design named it as a risk without a measurement, and a review refused that. Retransmission timers are the
  kernel's and differ by platform and setting. A reset leaves two cases at the moment at which the
  controller gives up: the listener has the connection in its queue, where the order of the queue and the
  stamp bound its begin, or the listener never receives the request. The kernel sends the same reset when
  the controller's process ends, because the option is set before the first byte is written; a test ends a
  child process without a close and reads the reset.
- **Applies to:** the connection of every handoff request (`runtime/loopback.py`), after an answer as well. A
  connect that fails and an option that cannot be set send nothing, and the failure says so. Health reads
  keep their ordinary close: they leave nothing behind that a listener could begin. The option is the
  controller's: an upgrade is requested by the command that is installed before it, so the upgrade from
  6.0.1 to this release runs without it.

### An unanswered request is settled when its timeout has passed at rest

- **Rests on:** the two decisions above. When the controller has given the request up, the request's
  connection is in the listener's queue or will never be. If it is in the queue, the listener accepts it
  before the connection of the resolver's first health read, and it answered that read no later than the
  read returned. So the request can begin no later than its timeout after that return, and a begin that
  passes the bound holds the session's lock until the session shows it: a read that is asked after that time
  shows every handoff that the request could begin.
  `tests/lifecycle/deployment/handoff/test_bound_window.py` runs the listener's handler and the resolver on
  one clock from both sides of the bound.
- **Considered:** wait the whole handoff deadline and end unknown, as a listener that a handoff created ends
  today; settle after the request's timeout; ask the listener whether it declares the bound and settle only
  then.
- **Chosen because:** the handoff deadline includes the predecessor's lease, up to the upstream timeout of 900
  seconds, and an unknown outcome leaves the operator a `recover` that then restores from the same reading. A
  declared capability would keep that path for a 6.0.1 listener alone and give it no safety: `recover` must
  end, and it restores. One rule for every listener is the smaller product; the proposal names the listener
  that does not enforce it.
- **Applies to:** the resolver and the proof. The wait is the timeout that the request carried and one second,
  which is far above the resolution of a platform's monotonic clock; it runs from the return of the first read
  that shows the captured listener at rest to the asking of a later read, and no other read starts it again.
  A handoff that begins inside it is followed as before.

### `recover` waits the longest timeout, and the journal keeps its form

- **Rests on:** `recover` reads health once and restores a transaction that reached activation and whose
  predecessor is at rest (`lifecycle/transaction/recovery.py`). A transaction is activated before its request
  is sent, so one that reached activation may have a request pending. The journal has a strict reader:
  `records.read_journal` refuses a field that it does not know. While a failed upgrade is restored, the
  installed command is the earlier release's again before the journal is closed:
  `completion.restore_prior_projection` restores the command, and `complete_transaction` writes the terminal
  journal after it, with every field of the journal that it was given.
- **Considered:** record the request and its timeout in the journal; wait the longest timeout that the
  protocol admits, 120 seconds.
- **Chosen because:** a new field would leave an upgrade from 6.0.1 whose restoration is interrupted with a
  journal that the installed command, 6.0.1's, refuses, and no command of that installation could close it.
  The longest timeout needs no record, and the wait arises only where a request may be pending and the
  listener is at rest.
- **Applies to:** `recover` of a transaction that reached activation and is not a first installation. One
  that did not reach activation requested nothing and is restored at once, as before. A restoration that
  would be refused is refused before the wait. The snapshot that is read after the wait is decided by the
  rules of the first: at rest it is restored, a successor that committed is finalized, and a handoff in flight
  or a listener that does not answer leaves the journal for a later `recover`, which waits again. The prior
  listener is at rest when its session is idle, or finalized with a transaction that is not the journal's: a
  process that has handed the journal's transaction off shows `serving` or that transaction while it serves
  out its lease, and can still answer a read on a connection that it accepted earlier. A successor has
  committed when its session is idle or finalized; one that shows `serving` waits for its predecessor's word
  to finalize, which can still abort it, and it ends on its own when that predecessor is gone. `recover`
  holds the lifecycle lock for the whole command, so no other lifecycle command runs beside the wait. In its
  human form it writes one line to standard error before the wait; with `--json` standard error stays as it
  is.

## Risks / Trade-offs

- [The upgrade to this release is requested by 6.0.1's command of 6.0.1's listener] → Nothing of this Change
  applies to that one handoff; it runs as every handoff of 6.0.1 does.
- [A listener of 6.0.1 or earlier does not bound its begin] → After a rollback to 6.0.1 the installed command
  is still this release's: it carries the evidence, the reset and the wait, and the wait is then a practical
  bound and not the listener's.
- [Windows was not measured] → The tests of the stamp, the queue's order and the reset run there in the hosted
  graph. What a full queue does there is not known; a connect that it refuses sends nothing, and the reset is
  set before the first byte on every platform.
- [The wait of an unanswered request is silent, and a second interrupt inside it preserves the transaction]
  → `recover` then waits its two minutes. The changelog says so; a line of progress for the three commands
  is not part of this Change.
- [A successor shows `finalized` from its predecessor's word on, and the predecessor can still fail while it
  reads the successor's answer to that word] → `recover` cannot tell that moment from a finished handoff and
  would finalize. Left as it is: it needs that failure in the instant of `recover`'s read, 121 seconds after
  a controller was lost.
- [A rollback's request names the retained generation, so two rollbacks to one generation share the
  transaction that "observed" reads] → An earlier request of that transaction can be pending only if a
  restore ran while it was; the wait in `recover` and the bound exclude that for a listener of this release.
- [A listener begins and ends a handoff between two polls of 0.1 seconds, unseen] → The request is then
  settled by its timeout instead of at once.
- [`recover` waits two minutes where the request's own timeout was thirty seconds, and where its controller
  had already waited the request out] → Accepted: it happens only after an interrupted or unconfirmed
  handoff, the journal says nothing of the request, and it stays readable to 6.0.1.

## Migration Plan

None. An upgrade from 6.0.1 and a rollback to it hand off as before.
