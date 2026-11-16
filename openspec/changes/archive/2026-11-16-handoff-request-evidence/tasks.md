## 1. The controller's evidence

- [x] 1.1 Carry on the failure of a handoff request what the controller holds of the listener's part in it
      (nothing sent, refused, acknowledged, observed, or nothing), and let the failure resolver and the proof
      before the restore read a listener at rest only together with it; a test forces the window through the
      listener's own handler and fails at 6.0.1, one test holds each row in the resolver and in the proof, and
      tests through upgrade, rollback and reload hold the rule for an unanswered request.
- [x] 1.2 End the connection of every handoff request with a reset, set before the first byte of the request
      is written, and read a failure of the connect or of that option as a request that was never sent;
      tests hold the order of connect, option and first byte, real-socket tests hold on each platform that
      the other end reads a reset, and no ordinary end of stream, after a request that was given up, after
      one that was answered and after a sender whose process ended, and a dated record holds what a full
      queue did on macOS and on Linux, with its scripts.

## 2. The listener's bound

- [x] 2.1 Stamp each accepted connection with the listener's monotonic clock in the accepting thread, and
      refuse a handoff request that reaches the begin of its handoff later than its own timeout after that
      stamp, under the lock that guards the handoff session and with that session unchanged; tests hold the
      refusal on both sides of the bound with a patched clock, and that the stamp is the accept's.
- [x] 2.2 Settle an unanswered request against a listener at rest once the request's timeout has passed since
      a health read that followed the request and the listener is still at rest; tests hold the wait, a
      handoff that begins inside it, and the three commands.

## 3. Recovery

- [x] 3.1 Let `recover` restore the prior selection of a transaction that reached activation only on a health
      read that it asks after the longest request timeout and one second have passed since its first read
      returned, decide that read by the rules of the first, and say in its human form on standard error that
      it waits; tests hold the wait and its length, each decision after it, the window through the listener's
      own handler, a transaction that requested no handoff, which is restored at once, and an interrupt, which
      leaves the journal and the selection.
- [x] 3.2 Let `recover` read the prior listener as at rest only when its session is idle or shows the
      finished handoff of another transaction, and a successor as committed only when its session is idle or
      finalized; tests hold each case on the first read and on the read after the wait, with the journal and
      the selection unchanged where `recover` refuses.

## 4. Release

- [x] 4.1 Prepare 6.0.2: `VERSION`, and the changelog heading dated by the UTC day.

## 5. Verification

- [x] 5.1 `mise run check` passes on the lane head.
- [x] 5.2 The hosted graph passes at the proposal head on both forges, with the journeys from the published
      6.0.1 on macOS, Linux and Windows.
- [x] 5.3 Each finding of a fresh-context review of the lane is fixed on the lane head or answered in the
      design.
