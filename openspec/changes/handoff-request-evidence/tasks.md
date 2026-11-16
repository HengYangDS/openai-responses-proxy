## 1. The controller's evidence

- [ ] 1.1 Carry on the failure of a handoff request what the controller holds of the listener's part in it
      (nothing sent, refused, acknowledged, observed, or nothing), and let the failure resolver and the proof
      before the restore read a listener at rest only together with it; a test forces the window through the
      listener's own handler and fails at 6.0.1, one test holds each row in the resolver and in the proof, and
      upgrade, rollback and reload each preserve an unanswered request's transaction.

## 2. The listener's bound

- [ ] 2.1 Stamp each accepted connection with the listener's monotonic clock in the accepting thread, and
      refuse a handoff request that reaches the begin of its handoff later than its own timeout after that
      stamp, under the lock that guards the handoff session and with that session unchanged; tests hold the
      refusal on both sides of the bound with a patched clock, and that the stamp is the accept's.
- [ ] 2.2 Settle an unanswered request against a listener at rest once the request's timeout has passed since
      a health read that followed the request and the listener is still at rest; tests hold the wait, a
      handoff that begins inside it, and the three commands.

## 3. Recovery

- [ ] 3.1 Let `recover` restore the prior selection of a transaction whose handoff may have been requested
      only after the longest request timeout has passed since it first read the listener at rest, and follow
      a handoff that begins in that time; tests hold the wait, the handoff that begins, and a transaction that
      requested no handoff, which is restored at once.

## 4. Release

- [ ] 4.1 Prepare 6.0.2: `VERSION`, and the changelog heading dated by the UTC day.

## 5. Verification

- [ ] 5.1 `mise run check` passes on the lane head.
- [ ] 5.2 The hosted graph passes at the proposal head on both forges, with the journeys from the published
      6.0.1 on macOS, Linux and Windows.
- [ ] 5.3 Each finding of a fresh-context review of the lane is fixed on the lane head or answered in the
      design.
