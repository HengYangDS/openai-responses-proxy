## 1. The route

- [x] 1.1 Add `test_shipped_manifest_routes_suheai_as_an_ordinary_provider` to `tests/providers/test_registry.py`: the
      shipped manifest routes `/suheai/v1/responses` to `https://suheai.cn/v1/responses` with native agent delivery
      and no wire policy. Done when it fails with `KeyError: 'suheai'` before the manifest changes. Failed so.
- [x] 1.2 Add `[providers.suheai]` with `base_url = "https://suheai.cn/v1"` to
      `src/openai_responses_proxy/providers/manifest.toml` (D1, D2). Done when the tests of `tests/providers` pass. 19
      passed.
- [x] 1.3 Add Suhe.AI to the README's route table and diagram. Done when the hard-coding and Markdown checks of
      `mise run quick` pass.
- [x] 1.4 Add the route under `### Added` in `CHANGELOG.md`'s `## [Unreleased]`. Done when Prettier's check of it
      passes.
- [x] 1.5 Pin `/suheai/v1/models` in the same test. Done when it passes. Passed.
- [x] 1.6 MODIFY "Provider-specific recovery is route-scoped", "Conversation state remains owned by Codex", "Client
      endpoint ownership is external" and "Provider rate limits do not multiply across retry layers" in general route
      terms, and point `docs/evidence/evidence-policy.md` at the acceptance scenario (D3). Done when
      `openspec validate suheai-interim-route --strict` passes. Valid.
- [x] 1.7 Change the switching scenario of `terminal-product-convergence`'s `provider-portable-responses` delta to
      "switches among provider routes", keeping its line count (D3). Done when both Changes validate strictly and a
      scratch archive of the two, in either order, merges specifications that pass `openspec validate --specs --strict`
      and name the providers only in the routes requirement and the Purpose. Both orders merged the same nine
      valid specifications, with the Purpose rewritten as D3 says.

## 2. Release

- [x] 2.1 Name 7.0.0 in `CHANGELOG.md` under the UTC date of its preparation, above a new empty `## [Unreleased]`
      (`VERSION` already reads 7.0.0). Done when `tests/release/test_metadata.py` passes. 54 passed.

## 3. Local verification

- [x] 3.1 Pass `mise run quick` and `tests/quality` as light work. Passed on `f8ac3638`: quick 166 tests;
      `tests/quality` with the provider and release metadata tests 679.
- [x] 3.2 Pass `openspec validate suheai-interim-route --strict`. Valid.
- [x] 3.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design. No critical and three important findings, answered in `f8ac3638`.
- [x] 3.4 Pass the full check (`mise run check`) on the lane head, in the host queue. Passed on `b0d85050`.

## 4. Exact-HEAD proof

- [x] 4.1 Pass the ETHOS full proof of the lane head. Proven for `b0d85050`, ten gates.

## 5. Hosted CI

- [x] 5.1 Pass the proposal's GitHub checks. Pull request 224: 15 passed, 5 skipped.
- [x] 5.2 Pass the proposal's GitLab pipeline. Pipeline 18476 passed.

## 6. Delivery

- [x] 6.1 Publish 7.0.0 to GitHub and GitLab: the signed tag `v7.0.0` and a Release on each with the `macos-arm64` and
      `linux-x86_64` assets and no Windows asset. The CHANGELOG heading carries the tag's UTC date; a later date is
      set in a commit of its own before the tag. Done when both Releases read back with those assets. Both read back
      the same six assets on 2026-11-26; the tag and the heading carry 2026-11-26.
- [x] 6.2 Install 7.0.0 on this host after telling the owner the window. Done when
      `openai-responses-proxy status --json` reports 7.0.0 and `GET /suheai/v1/models` through the listener returns
      Suhe.AI's own answer. Installed on 2026-11-26; the route returned Suhe.AI's own 401 without a token.
- [x] 6.3 AIGW points Codex's Suhe.AI provider at `http://127.0.0.1:8792/suheai/v1` (D4). Done when AIGW's projection
      for Codex reads that endpoint. Applied on 2026-11-27; Codex's Suhe.AI turns of 6.4 went through `/suheai/v1`.
- [x] 6.4 Accept the route (D4): an unchanged conversation completes at least two turns on each leg of DMXAPI to
      Suhe.AI to DMXAPI. Done when the evidence policy's prefix and metadata checks pass after every leg. Passed on
      2026-11-27: two turns per leg, prefix and metadata unchanged after each; the evidence is AIGW's.
