## 1. One transition

- [x] 1.1 Admit a runtime to a transition by one predicate, a valid identity and both handoff capabilities, in
      `install`, `rollback` and `reload`; tests show each refusing a runtime with one capability or none, with no
      projection, service call or process signal.
- [x] 1.2 Remove the native-generation replacement, the drain control routes, the admission drain state and the
      `proxy_draining` response; tests show the former route answered as an unrouted path with admission
      unchanged, and no source or test naming a removed shape.
- [x] 1.3 Keep `accepting: true` and `draining: false` in health as protocol 2 constants and drop the drain
      generation and lease fields; tests read the real health and status shape.
- [x] 1.4 Roll a failed handoff back only on read-only proof that the captured predecessor still serves; tests
      cover a predecessor that is gone, replaced, not accepting, draining and mid-handoff, each preserving the
      transaction.
- [x] 1.5 Close the transaction of a rollback that is refused before any projection, through the owner that
      `install` uses; a test with a real transaction shows no journal, an unchanged selector and an unchanged
      installed record.
- [x] 1.6 Count a listener's finalized handoff session, exactly as captured, as no handoff begun in the failure
      resolver and in the proof; tests show a refused request resolved at once for such a listener, and a session
      in flight or of another transaction left unresolved.
- [x] 1.7 Bound both durations of a handoff request alike and read a health response that breaks off as no
      runtime; tests cover values that are no finite number and a response cut after its headers.

## 2. Verification

- [ ] 2.1 `mise run check` passes on the lane head.
- [ ] 2.2 The hosted graph passes at the proposal head, with the journeys from the published 5.1.12 on macOS, Linux
      and Windows; the head carries the next `VERSION`, or the journeys would start from 5.1.11.
- [ ] 2.3 Each finding of a fresh-context review of the lane is fixed on the lane head or answered in the design.
