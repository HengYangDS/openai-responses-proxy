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
- [x] 2.3 Add recovery tests: `invalid_encrypted_content` on the unchanged body is followed by one projected re-send;
      a second refusal reaches the client; a portable route relays the refusal without a re-send; a capacity recovery
      on a pass-through route derives from the projected body. Done when the re-send tests fail before 2.4 and all
      pass after it. Both re-send tests failed, then passed.
- [x] 2.4 Implement the re-send and start every request-changing recovery from the projected body (D3). Done when
      2.3's tests pass. Passed.

## 3. Documents and release

- [x] 3.1 Amend DR-0002 with the declared pass-through shape, and describe it in the README's route section. Done when
      governance and the Markdown checks pass. Passed.
- [x] 3.2 Set `VERSION` to 7.0.1 and add the release to `CHANGELOG.md`. Done when `tests/release/test_metadata.py`
      passes. Passed.

## 4. Verification

- [x] 4.1 Pass `mise run quick`, the relay and provider tests, and `openspec validate dmxapi-pass-through --strict`.
      Quick 166 passed; valid.
- [ ] 4.2 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered
      in code or in the design.
- [ ] 4.3 Pass the ETHOS full proof of the lane head.
- [ ] 4.4 Pass the proposal's GitHub checks and GitLab pipeline.

## 5. Delivery

- [ ] 5.1 Publish 7.0.1 to GitHub and GitLab: the signed tag `v7.0.1` and a Release on each with the platform
      assets. Done when both Releases read back with those assets.
- [ ] 5.2 Install 7.0.1 on this host after telling the owner the window. Done when
      `openai-responses-proxy status --json` reports 7.0.1.
- [ ] 5.3 Accept the fix: a new Codex conversation on DMXAPI through the installed proxy completes two turns with
      reasoning tokens above 0. Done when the turn records show them.
