## 1. Pin

- [x] 1.1 Pin `@fission-ai/openspec` 2.2.0 in `package.json` and resolve `package-lock.json` with the selected npm,
      scripts ignored (D1). Done when the lock changes only OpenSpec and its nine added packages, equals the lock that
      a throwaway copy resolved for the advisory check, and `npm exec --offline -- openspec --version` prints 2.2.0.
      npm 12.2.3 wrote that lock, and `npm audit --json` on the copy reported no advisory before or after the change.
- [x] 1.2 Record the move under `## [Unreleased]` in `CHANGELOG.md`. Done when governance and the prose gate pass.

## 2. Close the previous Change

- [x] 2.1 Tick `relay-hygiene` task 5.3 with its evidence and archive the Change with `ethos lane archive-change`.
      Depends on `relay-hygiene` having landed on both Forges' `dev` with green pipelines. Done when the archive holds
      its specifications and `openspec validate --all --strict` passes. Archived in `8e8384a2`; 2.2.0 passed 10 of 10
      items.

## 3. Verification

- [ ] 3.1 Pass `openspec validate --all --strict` with 2.2.0 and ETHOS's readiness proof, which validates and
      simulates the archive with 2.0.0. Done when both pass on the head that the proof proves.
- [x] 3.2 Answer one fresh independent review of the whole Change in code or design. Done when every finding has an
      answer. Reviewed on 2026-11-28 at `fe3c78ea`; its ten findings are answered in this Change's documents and
      `CHANGELOG.md`.
- [ ] 3.3 Pass the ETHOS full proof under a host ticket. Done when the proof of the lane head passes.
