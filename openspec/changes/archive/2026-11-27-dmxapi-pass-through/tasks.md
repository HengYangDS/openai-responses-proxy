## 1. Manifest and registry

- [x] 1.1 Add tests to `tests/providers/test_registry.py`: `request_shape` defaults to `portable`, the shipped
      manifest gives `dmxapi` `pass-through` and every other route `portable`, an unknown value and pass-through with
      plaintext delivery fail the load. Done when they fail before 1.2 and pass after it. Four failed, then passed.
- [x] 1.2 Add `request_shape` to `Profile` and the manifest parser, and declare `pass-through` for `dmxapi` (D1).
      Done when 1.1's tests pass. Passed.

## 2. Relay

- [x] 2.1 Add relay tests: a stateless Codex body to a pass-through route reaches the upstream byte for byte; a body
      without `store=false` or with `previous_response_id` gets the projected form; a body the projection rejects is
      refused locally with no upstream request; a portable route is unchanged. Done when they fail before 2.2 and
      pass after it. The byte-for-byte test failed, then passed; the other three already held and stay as guards.
- [x] 2.2 Send the client's bytes on a pass-through route when the request is stateless, keeping the projected body
      on the exchange (D2). Done when 2.1's tests pass. Passed.
- [x] 2.3 Add recovery tests: any HTTP 400 to the client's bytes, `invalid_encrypted_content` included, is followed by
      one projected attempt; a second refusal reaches the client; a portable route relays the refusal without a
      re-send; a transient error and DMXAPI's 477 retry with the projection; a capacity refusal of the projection
      leads to a smaller request derived from it; an item reference makes a request stateful; stripped-item counters
      follow the sent body; a `Profile` refuses pass-through with plaintext delivery. Done when they fail before 2.4
      and pass after it. All failed, then passed.
- [x] 2.4 Send the client's bytes at most once per request, answer a 400 to them with one projected attempt, and count
      projection telemetry only for projected sends (D2, D3). Done when 2.3's tests pass. Passed.

## 3. Documents and release

- [x] 3.1 Amend DR-0002 with the declared pass-through shape, and describe it in the README's route section. Done when
      governance and the Markdown checks pass. Passed.
- [x] 3.2 Set `VERSION` to 7.0.1 and add the release to `CHANGELOG.md`. Done when `tests/release/test_metadata.py`
      passes. Passed.

## 4. Verification

- [x] 4.1 Pass `mise run quick`, the relay and provider tests, and `openspec validate dmxapi-pass-through --strict`.
      Quick 166 passed; valid.
- [x] 4.2 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered
      in code or in the design. Two important and five minor findings; all answered in code, the specification or the
      design, duplicate JSON keys by a stated risk.
- [x] 4.3 Pass the ETHOS full proof of the lane head. Passed on 8094cf82 (10 gates, attestation e674af9c).
- [x] 4.4 Pass the proposal's GitHub checks and GitLab pipeline. Pull request 227: 15 passed, 0 failing; GitLab
      dev pipeline 18534 and main pipeline 18537 succeeded.

## 5. Delivery

- [x] 5.1 Publish 7.0.1 to GitHub and GitLab: the signed tag `v7.0.1` and a Release on each with the platform
      assets. Done when both Releases read back with those assets. Both Releases read back the same six assets.
- [x] 5.2 Install 7.0.1 on this host after telling the owner the window. Done when
      `openai-responses-proxy status --json` reports 7.0.1. Installed 11:47:33–11:47:53; status reported 7.0.1.
- [x] 5.3 Run the fix's acceptance: a new Codex conversation on DMXAPI through the installed proxy, two turns, and
      decide from the turn records. Done when the records are read and the owner has decided. The criterion,
      reasoning tokens above 0 in each turn, was not met (31, then 0); an alternating measurement gave 0 on every
      path, direct included (design, "Outcome"). The owner withdrew the route (section 6).

## 6. Withdrawal (7.0.2)

- [x] 6.1 Restore the code, tests, README and architecture document to their state before 7.0.1, drop the
      specification delta and declare `skip_specs` (D4). Done when no source or test names a request shape and
      `openspec validate dmxapi-pass-through --strict` passes. Restored from a5e682e3; valid with `skip_specs`.
- [x] 6.2 Record the amendment and its withdrawal in DR-0002, set `VERSION` to 7.0.2 and add the release to
      `CHANGELOG.md`. Done when governance and `tests/release/test_metadata.py` pass. Both passed.
- [x] 6.3 Pass `mise run quick`, the relay and provider tests, Vale and Nox quality. Done when each exits 0.
      Quick 166, providers and metadata 73, relay 403 in five groups, protocol 329 passed; Vale and quality 0.
- [x] 6.4 A fresh review of the withdrawal by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design. One important finding (the record stated an unmeasured cause; corrected in the
      changelog, DR-0002, proposal and design), four minor (DR-0002's revisit condition, the cumulative per-turn
      figures, the evidence references, a relay test for a stateless request on every route, which fails when the
      relay forwards the client's bytes) and four nits, all answered.
- [x] 6.5 Pass the ETHOS full proof of the lane head. Passed on f89a05c1 (10 gates, attestation 55b3d91e).
- [x] 6.6 Pass the proposal's GitHub checks and GitLab pipeline. Pull request 228: 15 passed, 0 failing; GitLab
      dev pipeline 18557 and main pipeline 18560 succeeded.
- [x] 6.7 Publish 7.0.2 to GitHub and GitLab: the signed tag `v7.0.2` and a Release on each with the platform
      assets. Done when both Releases read back with those assets and the changelog's date for 7.0.2 is the tag's
      UTC date. Both Releases read back the same six assets; the changelog and the tag share 2026-11-27 UTC.
- [x] 6.8 Install 7.0.2 on this host after telling the owner the window. Done when
      `openai-responses-proxy status --json` reports 7.0.2. Installed 15:00:19–15:00:36; status reported 7.0.2.
- [x] 6.9 Accept the withdrawal. Done when the proxy log shows a DMXAPI request through the installed 7.0.2 sent as
      projected, without the `unchanged` note. A request without a token at 16:56:47 was logged as projected, its
      message identifier and `include` entry removed, in one attempt; DMXAPI answered 401.
