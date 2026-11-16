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
  | A failure before the call that opens the connection        | unsent       | idle, or the captured session   |
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

- **Rests on:** POSIX `accept` takes "the first connection on the queue of pending connections", and Winsock's
  `accept` states the same; a test accepts connections that were made in a known order and holds that order
  on each platform of the hosted graph. One thread of one process accepts for a listener at rest
  (`ResilientProxyServer.get_request`). Python's `time.monotonic` is system-wide, so two processes of one host
  measure the same durations. The session's lock guards the begin and the read of the session for health; a
  test holds a begin inside the lock and shows that a health read waits for it.
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

### An unanswered request is settled when its timeout has passed at rest

- **Rests on:** the bound above. The request's connection was accepted before the resolver's first health
  read was. So the request can begin no later than its timeout after that read's answer, and a begin that
  passes the bound holds the session's lock until the session shows it.
- **Considered:** wait the whole handoff deadline and end unknown, as a listener that a handoff created ends
  today; settle after the request's timeout; ask the listener whether it declares the bound and settle only
  then.
- **Chosen because:** the handoff deadline includes the predecessor's lease, up to the upstream timeout of 900
  seconds, and an unknown outcome leaves the operator a `recover` that then restores from the same reading. A
  declared capability would keep that path for a 6.0.1 listener alone and give it no safety: `recover` must
  end, and it restores. One rule for every listener is the smaller product; the proposal names the listener
  that does not enforce it.
- **Applies to:** the resolver and the proof. The wait is the timeout that the request carried, and a
  handoff that begins inside it is followed as before.

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
- **Applies to:** `recover` of a transaction that reached activation. One that did not requested nothing and
  is restored at once, as before.

## Risks / Trade-offs

- [A listener of 6.0.1 or earlier does not bound its begin] → The wait is then a practical bound and not the
  listener's. It concerns every handoff from such a listener: the upgrade to this release, and a transition
  that follows a rollback to 6.0.1.
- [A rollback's request names the retained generation, so two rollbacks to one generation share the
  transaction that "observed" reads] → An earlier request of that transaction can be pending only if a
  restore ran while it was; the wait in `recover` and the bound exclude that for a listener of this release.
- [A listener begins and ends a handoff between two polls of 0.1 seconds, unseen] → The request is then
  settled by its timeout instead of at once.
- [`recover` waits two minutes where the request's own timeout was thirty seconds] → Accepted: it happens
  only after an interrupted or unconfirmed handoff, and the journal stays readable to 6.0.1.

## Migration Plan

None. An upgrade from 6.0.1 and a rollback to it hand off as before.
