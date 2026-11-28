## 1. Close the previous Change

- [ ] 1.1 Tick `relay-hygiene` task 5.3 with its evidence and archive the Change with `ethos lane archive-change`.
      Done when both Forges' `dev` pipelines on its head are green and the archive holds its specifications.

## 2. Pin

- [x] 2.1 Pin `@fission-ai/openspec` 2.2.0 in `package.json` and resolve `package-lock.json` with the selected npm,
      scripts ignored (D1). Done when the lock changes only OpenSpec and its nine added packages, and
      `npm exec --offline -- openspec --version` prints 2.2.0. npm 12.2.3 wrote the same lock as the audited copy,
      and the command prints 2.2.0.
- [x] 2.2 Record the move under `## [Unreleased]` in `CHANGELOG.md`. Done when governance and the prose gate pass.

## 3. Verification and landing

- [x] 3.1 Pass `openspec validate --all --strict` with 2.2.0 and the archive simulation of ETHOS's readiness proof.
      Done when both pass on the lane head. On `195c2464`, 2.2.0 passed 11 of 11 items, and the readiness proof
      left only `full_proof_requires_execute`.
- [ ] 3.2 Answer one fresh independent review of the whole Change in code or design. Done when every finding has an
      answer.
- [ ] 3.3 Pass the ETHOS full proof under a host ticket and land on both Forges' `dev`. Done when both `dev` branches
      hold the head and their pipelines are green.
