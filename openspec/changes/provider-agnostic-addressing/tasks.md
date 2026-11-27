## 1. Addressing

- [ ] 1.1 Add `tests/relay/test_addressing.py` for `relay/addressing.resolve`: accepted HTTPS targets with a query, a
      port, no base path, and loopback `http` including `[::1]`; refused remote `http`, user information, upper-case
      scheme, unknown resources, empty and dot segments, percent-encoding, fragments and absolute form. Done when
      they fail before 1.2 and pass after it.
- [ ] 1.2 Implement `relay/addressing.py` with `Target` and `resolve` (D1). Done when 1.1's tests pass.

## 2. HTTP 477

- [ ] 2.1 Add a relay test: an HTTP 477 is relayed once, with no retry and no cooldown. Done when it fails before 2.2
      and passes after it.
- [ ] 2.2 Remove the DMXAPI wire policy, `relay/wire_recovery.py`, the wire-failure counters and the 477 exclusion in
      `relay/exchange.py` (D2). Done when 2.1's test and the relay tests pass.

## 3. Serving by upstream origin

- [ ] 3.1 Add relay tests: a data request with `Origin` or a non-loopback `Host` is refused before upstream I/O;
      cooldowns of two origins on one host stay independent; a model-catalog GET keeps its query; a log line names
      `upstream=` and no provider. Done when they fail before 3.2 and pass after it.
- [ ] 3.2 Resolve every data request through `addressing.resolve`, key cooldowns and log lines by origin, rename the
      route error codes, admit data requests through the local-client rule, delete `providers/`, and move the test
      fixtures to origin-addressed paths (D1, D2). Done when 3.1's tests and every test group pass.

## 4. Agent delivery

- [x] 4.1 Record M4's result in the design and write the plaintext requirement's delta (D2). Done when the Change
      passes `openspec validate --strict`. M4 passed on 2026-11-27: UCloud handled an encrypted follow-up message,
      so the requirement is removed.
- [ ] 4.2 Add a relay test that the native `collaboration` catalog reaches every upstream unchanged, then remove the
      plaintext delivery mode and its tests (D2). Done when the test fails before the removal and the relay and
      protocol tests pass after it.

## 5. Upstream override and payload

- [ ] 5.1 Add tests: a 7.0.2 runtime carrier is accepted; one with `upstream_base_url` is refused;
      `--upstream-base-url` is unknown; the bundle inventory holds no `providers.toml`. Done when the new refusals
      fail before 5.2 and all pass after it.
- [ ] 5.2 Remove `--upstream-base-url`, `upstream_base_url`, `providers.toml` from the payload and bundle, the
      `network-provider-route` control and the provider names in the performance tools (D1). Done when 5.1's tests
      and the lifecycle, CLI, release, performance and quality tests pass.

## 6. Documents and release metadata

- [ ] 6.1 Amend DR-0001 and DR-0003, and update the README, the architecture document and the evidence policy (D4).
      Done when governance, Vale and the Markdown checks pass.
- [ ] 6.2 Set `VERSION` to 8.0.0 and add the release to `CHANGELOG.md` under `Breaking`, `Removed` and `Changed`. Done
      when `tests/release/test_metadata.py` passes.

## 7. Verification and release

- [ ] 7.1 Run the full local checks; no provider name remains in `src`, `tests` or `tools`. Done when they pass.
- [ ] 7.2 Answer one fresh independent review of the whole Change in code or design. Done when every finding has an
      answer.
- [ ] 7.3 Pass the ETHOS full proof under a host ticket and land on both forges. Done when both forges' `dev` and
      `main` hold the release commit.
- [ ] 7.4 Publish the signed tag `v8.0.0` and the Releases on both forges. Done when both Releases list the bundle.
- [ ] 7.5 Hand AIGW's maintainer the D3 contract. Done when AIGW's change is released.

## 8. Cutover and acceptance

- [ ] 8.1 Install 8.0.0 after AIGW's release, in a window the owner is told first. Done when `status --json` reports
      8.0.0 and AIGW has re-projected the base URLs.
- [ ] 8.2 Accept: the owner's next DMXAPI turns are logged with `upstream=https://www.dmxapi.cn/v1`, projected and
      completed; one request without a token to each other origin reaches that upstream's own authentication error.
      Done when all four are recorded.
- [ ] 8.3 Rewrite the specification's Purpose in upstream terms, then archive the Change. Done when
      `openspec validate --specs --strict` passes after the archive.
