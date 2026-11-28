## 1. Tests

- [x] 1.1 Replace the source-build supply tests in `tests/forge/workflows/test_supply.py` with tests that both Forges'
      commit-admission jobs install only the locked ETHOS through Mise, bind trust with `ethos hook bind-trust` and
      run `ethos hook commit-range` from it, with no ETHOS source checkout or `PYTHONPATH`; that the Mise
      configuration pins one exact `github:HengYangDS/ethos` release with `locked_verify_provenance`; that the lock
      holds it for every lock platform with attestation provenance; and that no other Mise job installs it. Done when
      they fail before 2.1 and pass after 3.1.

## 2. Pin

- [x] 2.1 Select 0.2.0-beta.12 in this lane with its `ethos hook install --runtime`, write the pin with
      `ethos lane repair-paradigm --pin` and lock it with `mise lock` (D1, D3). Done when the lock gains only the
      ETHOS entries and `ethos status` in the lane reports the pinned release.

## 3. CI

- [x] 3.1 Supply the commit-admission jobs from the pin and enable only declared tools in every other Mise job, in
      the CUE sources, and regenerate both workflows (D2). Done when 1.1's tests and the forge and CI tests pass.
- [x] 3.2 State the one ETHOS selection in CONTRIBUTING and add the change to `CHANGELOG.md` under Unreleased. Done
      when governance and the prose gate pass.

## 4. Verification and landing

- [ ] 4.1 Answer one fresh independent review of the whole Change in code or design. Done when every finding has
      an answer.
- [ ] 4.2 Pass the ETHOS full proof under a host ticket and land on both Forges' `dev`; both commit-admission jobs
      pass on the landed commit. Done when GitHub's and GitLab's `dev` hold it with those jobs green.
