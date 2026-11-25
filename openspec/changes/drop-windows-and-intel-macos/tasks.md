## 1. Specifications

- [x] 1.1 Write the deltas of `ci-diagnostics`, `runtime-upgrade`, `product-interface`, `process-ownership` and
      `quality-boundaries`, with the platform declaration and the GitLab macOS job of D2 (D8). Done when
      `openspec validate drop-windows-and-intel-macos --strict` passes.
- [x] 1.2 Edit the open tasks 10.2, 12.5 and 13.5, the four deltas, the proposal and the design of
      `terminal-product-convergence` so that nothing in them requires Windows, keeping every task identity and state
      (D6). Done when both Changes validate strictly and the lane's archive rehearsal of this Change and then that one
      merges specifications that validate and name no Windows platform.

## 2. Product code

- [x] 2.1 Reduce `RELEASE_PLATFORMS` to `macos-arm64` and `linux-x86_64` and remove the `windows` parameter of
      `executable_name` and of the executable inventory (D1). Done when no caller passes it and the product package
      imports.
- [x] 2.2 Delete the Task Scheduler adapter and its tests, so that the service adapter is launchd or systemd (D3).
      Done when `lifecycle/supervision/windows.py` and its test module are absent and the supervision tests pass.
- [x] 2.3 Remove every other Windows branch of `src/` named in D3 and collapse each seam left with one implementation,
      keeping the refusal of Windows-style names in untrusted archive and path input (D3). Done when no module under
      `src/` reads `os.name`, `winerror`, `ntpath`, `PureWindowsPath` or a Windows environment variable outside that
      input validation, and the tests of `tests/lifecycle`, `tests/serving`, `tests/runtime` and `tests/cli` that start
      no build pass.

## 3. Repository tools

- [x] 3.1 Make release construction, bundle, format, signing, assembly, the publication evaluator, the observed hosted
      job names, `tools/quality` and `tools/performance` follow the supported set, and delete the `ctypes`
      PyInstaller hook if nothing in the bundle needs it (D1, D3). Done when no tool module names Windows outside
      archive input validation and the release tests that start no build pass.
- [x] 3.2 Pin by test that a predecessor whose signed checksum manifest also names a Windows asset is installed and
      verified for its own platform without error (D4). Done when that test passes.

## 4. Tests

- [x] 4.1 Delete the Windows-only tests, make the POSIX-only skips unconditional, and remove the Windows
      parametrizations, mocks, fixtures, drive letters and UNC paths; tests that enumerate platforms or CI jobs derive
      them from the declarations (D3). Done when a test names Windows only to show that it is refused or in the
      predecessor test of task 3.2, and the affected test files that start no build pass.

## 5. CI declaration

- [x] 5.1 Declare each platform's runner per Forge once in `.config/ci/graph/`, render GitHub's macOS jobs on
      `macos-latest` from it, and remove the `python-windows` job and the Windows legs and assembly input (D2). Done
      when `python -m tools.ci.project` reports no drift on the regenerated projections and the `tests/forge` and
      `tests/ci` tests pass.
- [x] 5.2 Add GitLab's macOS Python job from the same job declaration, tagged `macos-latest` with `review` or
      `protected`, with its own image, tag and cache override and a Mise bootstrap, and with failure allowed until
      task 5.3 (D2). Done when the generated `.gitlab-ci.yml` holds the job for both roles and the GitLab workflow
      tests pass.
- [ ] 5.3 Measure on GitLab's macOS guest which of Python 3.12, 3.13 and 3.14 Mise and `uv python install` provide
      there, then declare the macOS job required (D2). Done when one review pipeline's macOS jobs pass and the
      declaration no longer allows their failure.

## 6. Toolchain and configuration

- [x] 6.1 Restrict `uv.lock` to macOS ARM64 and Linux through uv's own environment setting (D5). Done when the lock's
      diff removes only entries outside those platforms, rewrites markers only to forms equivalent on them, and changes
      no kept version, hash or URL (measured in the design, D5).
- [x] 6.2 Restrict `.config/mise/mise.lock` to macOS ARM64 and Linux through Mise's own platform setting, and drop the
      Windows template of `.config/mise/config.toml` (D5). Done when the lock's diff only removes `macos-x64` and
      `windows-x64` rows.
- [x] 6.3 Remove the coverage and Ruff configuration that served only Windows. Done when the policy tests that read
      them pass.

## 7. Documents and records

- [x] 7.1 State the supported set once and link to it, remove Windows from the current documents, and keep
      `docs/evidence/windows-real-machine-validation.md` as dated evidence (D7). Done when no current document names
      Windows as supported and the Markdown and link checks pass.
- [x] 7.2 Add a dated amendment line naming this Change to each decision record that names Windows as current
      behavior and cite each record in the design, set `VERSION` to 7.0.0 and add a breaking entry under
      `## [Unreleased]` in `CHANGELOG.md` (D7). Done when `python -m tools.release.metadata` passes.

## 8. Local gates

- [ ] 8.1 Pass the proxy's full check (`mise run check`) on the lane head, in the host queue.
- [ ] 8.2 Pass the native macOS run (`mise run native`) on the lane head, in the host queue.
- [ ] 8.3 A fresh review of the whole change by a reviewer who has not seen it. Done when its findings are answered in
      code or in the design.

## 9. Exact-HEAD proof

- [ ] 9.1 Pass the ETHOS full proof of the lane head.

## 10. Hosted CI

- [ ] 10.1 Pass the proposal's GitHub checks, and read the macOS version and architecture from the first `macos-latest`
      job's log.
- [ ] 10.2 Pass the proposal's GitLab pipeline on the Linux runner, with its macOS jobs run on the runner tagged
      `macos-latest` and `review`.

## 11. Publication

- [ ] 11.1 Before the first `dev` push to GitHub, remove the Windows checks from the required checks of `dev` and
      `main`, keeping the other names. Done when both protections are read back.
- [ ] 11.2 Before the first GitLab push, obtain ETHOS 仓库纳管's confirmation that the proxy's GitLab project has
      macOS runners tagged `macos-latest` with `review` and with `protected`.
- [ ] 11.3 Tell ETHOS 仓库纳管 when this Change is on `dev` and `main` of both Forges, for its Windows guest step.

## 12. Housekeeping

- [ ] 12.1 Retire `build/evidence/proxy-windows-removal-inventory-20261125.md` in aigw-cli's main checkout once the
      review of task 8.3 has used it. Done when the file is absent.
