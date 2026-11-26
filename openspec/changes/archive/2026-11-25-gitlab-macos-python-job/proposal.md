## Why

Each project holds with no remote, one remote or two, and where the two forges overlap their declaration is one
mirrored declaration (the owner, 2026-11-25 13:47, relayed). GitHub runs the proxy's Python verification on macOS
(`macos-latest`); GitLab runs it only on Linux, because the proxy's GitLab project had no macOS runner. The Change
`drop-windows-and-intel-macos` therefore left GitLab's macOS job to this Change (its design D2). On 2026-11-26 at 04:48
the owner agreed to register two macOS runners for the proxy (relayed by ETHOS 仓库纳管), and they were registered and
read back at 04:55 to 04:56: `macos-latest` with `review` (not protected) and `macos-latest` with `protected`
(ref_protected).

## What Changes

- GitLab runs the proxy's Python verification (3.12, 3.13, 3.14) on its ARM64 macOS guest, as GitHub runs it on
  `macos-latest`, from the same provider-neutral platform declaration: the review role on `macos-latest` + `review`,
  the protected role on `macos-latest` + `protected`. The job overrides GitLab's default image and tags and does not
  inherit the global variables; it bootstraps its interpreters with Mise, not `apt-get`.
- The job is required from the start: a failure fails the pipeline.
- The specification says GitLab and GitHub both verify the supported Python versions on macOS from one declaration.

Not breaking.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-diagnostics`: GitLab verifies Python on macOS from the same platform declaration as GitHub.

## Impact

`.config/ci/` (the GitLab adapter and the platform declaration), the generated `.gitlab-ci.yml`, CI structure tests,
the CI documents, the accepted `ci-diagnostics` specification. Out of scope: native assets, the release lifecycle and
assembly on GitLab (GitHub alone builds them, as today); the CI and runner paradigm that ETHOS 产品构建 designs.
