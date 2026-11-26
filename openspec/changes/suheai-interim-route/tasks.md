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

## 2. Release

- [x] 2.1 Name 7.0.0 in `CHANGELOG.md` under the UTC date of its preparation, above a new empty `## [Unreleased]`
      (`VERSION` already reads 7.0.0). Done when `tests/release/test_metadata.py` passes. 54 passed.

## 3. Local verification

- [ ] 3.1 Pass `mise run quick` and `tests/quality` as light work.
- [ ] 3.2 Pass `openspec validate suheai-interim-route --strict`.
- [ ] 3.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design.
- [ ] 3.4 Pass the full check (`mise run check`) on the lane head, in the host queue.

## 4. Exact-HEAD proof

- [ ] 4.1 Pass the ETHOS full proof of the lane head.

## 5. Hosted CI

- [ ] 5.1 Pass the proposal's GitHub checks.
- [ ] 5.2 Pass the proposal's GitLab pipeline.
