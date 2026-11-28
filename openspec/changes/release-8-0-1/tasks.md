## 1. Close the previous Change

- [x] 1.1 Tick `openspec-2-2-0` task 3.3 with its evidence and archive the Change with `ethos lane archive-change`.
      Done when the archive holds it and `openspec validate --all --strict` passes. Archived in `b83efac6`.

## 2. Release record

- [x] 2.1 Move the OpenSpec 2.2.0 entry from Unreleased to the end of `### Changed` under `## [8.0.1]` (D1). Done when
      the release metadata check and governance pass. `tools.release.metadata --prepare-release` reports 8.0.1 OK.

## 3. Verification

- [ ] 3.1 Pass the ETHOS full proof under a host ticket. Done when the proof of the lane head passes.
