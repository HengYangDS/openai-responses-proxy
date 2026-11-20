## Why

`single-test-run` declared three 3.12 reports on the `python-quality` gate that the gate cannot use. The
`quality` session names its reports by the session, `quality`, without a Python suffix
(`quality-runtime.json` and `quality-coverage.xml` are pinned by
`tests/quality/orchestration/test_observation.py`), so the declared `quality-3.12.*` files are never written.
Under ETHOS 0.2.0-beta.10 the correct names would still be refused: a session that declares JUnit evidence
needs a pytest invocation in its runtime record, and the gate's one record for that session is Ruff's. The
acceptance owner of this repository (the AIGW and Proxy session) chose on 2026-11-20 that the proof judges
3.12 through the `python-quality` gate's exit status and its coverage floor.

## What Changes

- `python-quality` returns to its declaration before `single-test-run`: the static verifier alone, its own
  dimensions, its Nox report, SARIF and Ruff runtime record.
- `python-matrix` keeps running only `tests-3.13` and `tests-3.14`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None; this Change declares `skip_specs: true`. The requirements nearest to it stay met:
`ci-diagnostics` "Python compatibility and native release prove distinct facts" (each supported minor line
runs the complete non-native behaviour inventory: 3.12 in `quality`, 3.13 and 3.14 in `tests`, all three in
hosted CI); `product-interface` "Repository-owned verification separates wheel compatibility from native
distribution" (the `quality` session installs the wheel and runs the inventory through it); and
`release-governance`'s independent test node per supported Python, which governs the unchanged Forge graphs.

## Impact

`.ethos/profile.toml` only.

- **Authority boundary:** unchanged from `single-test-run`; the AIGW and Proxy session accepts the change.
- **Non-goals:** the noxfile, the CI graphs and the coverage policy. One Nox session that carries both static
  and behaviour evidence is a need of ETHOS, which the AIGW and Proxy session raises with the ETHOS session.
