## Context

The proposal gives the owner's words and the scope. A read-only inventory of `dev` at `92e31741` is kept at
`build/evidence/proxy-windows-removal-inventory-20261125.md` in aigw-cli's main checkout; file and line references below
are from it. CI is generated from one CUE model (`.config/ci/`, rendered by `tools/ci/project.py`); release identity is
`RELEASE_PLATFORMS` in `src/openai_responses_proxy/product_identity.py`.

## Goals / Non-Goals

**Goals:** one supported set (macOS ARM64 and Linux) declared once per concern; no code, test, pipeline, lock row,
document or requirement that exists only for Windows or Intel macOS; GitLab and GitHub run the same macOS Python job from
one declaration.

**Non-Goals:** a Linux ARM64 release asset; native assets or the launchd lifecycle on GitLab; the CI and runner paradigm
that ETHOS 产品构建 designs for every governed repository (owner, 19:10 and 19:23 relayed), which reaches the keeper
before it touches this repository; the CI guests and runner registration.

## Decisions

**D1. The supported set.** `RELEASE_PLATFORMS` becomes `macos-arm64` and `linux-x86_64`. Assembly, the publication
evaluator, the hosted job names that the proof expects, and `executable_name` follow it; `executable_name` loses its
`windows` parameter. Alternative rejected: adding `linux-arm64` now, because no one asked for it and it widens the
Change. With no parameter left, `executable_name` is `COMMAND_NAME` itself, and the inventory's executable and
required files are constants (D3's collapse rule).

**D2. One platform declaration for CI.** A provider-neutral declaration in `.config/ci/graph/` names each platform's
runner per forge: GitHub `macos-latest` and `ubuntu-24.04`; GitLab `macos-latest` with `review` for merge requests and
`protected` for `dev`, `main` and tags, and the existing Linux runner variables. The GitHub adapters stop writing
`macos-26` literals. GitLab gains the macOS Python verification job that GitHub runs today (3.12, 3.13, 3.14), rendered
from the same job declaration, with its own default-image and default-tag override and a Mise bootstrap instead of
`apt-get`; its interpreters come from Mise or `uv python install` as measured on the guest before the job is declared
required. Native assets, the release lifecycle and assembly stay on GitHub, where they run today; they are not an
overlap. The declaration is `.config/ci/graph/platforms.cue`: each platform's release asset, its GitHub runner and its
GitLab tags per role, with `required: false` for the GitLab macOS job, which the pipeline renders as
`allow_failure: true` until task 5.3 measures the guest. The GitLab job inherits neither the default nor the global
variables, so no Linux image, tag, cache key or target reaches it; it sets its own uv cache directories and its target
`macos-arm64`, which names its cache.

**D3. Code.** `lifecycle/supervision/windows.py` (Task Scheduler) and its tests leave; the supervision adapter choice is
launchd or systemd. Every `sys.platform`/`os.name` Windows branch, `InstallationContext.windows` and the `windows=`
parameters, the `.cmd` launcher, `LOCALAPPDATA` defaults, `ntpath`/`PureWindowsPath`, the handoff `socket.share` path
and its `listener_share_b64` field, the watchdog's detach flags, the kernel32 probe, `winerror` retries and the Windows
`SO_LINGER` packing leave. A seam with one remaining implementation collapses into it. Archive validation that refuses
Windows-style member names stays: it guards untrusted input, whatever host reads it. The handoff protocol keeps
version 2, because a POSIX parent never sent `listener_share_b64`. The `ctypes` PyInstaller hook stays: the bundle
still reaches `ctypes` through `rich._windows` and the standard library's `platform` module, whose imports PyInstaller
follows statically (read in the locked packages, 2026-11-25). Two POSIX readings change with the Windows code: a root
override that starts with `~\` is no longer the home directory, and a backslash in an override is a filename character,
not a separator; and the child-environment filter matches product and Python variable names case-sensitively, as POSIX
Python and the product read them. The CHANGELOG states the first, and tests pin both.

**D4. Published predecessors.** Releases up to 6.0.3 carry a Windows asset. A command that verifies such a release
(the published-release compatibility check and any upgrade from it) verifies the platforms the current product
declares and ignores assets outside them without treating them as an error; the evaluator's "exactly the declared set"
rule applies to releases built from this Change on. A release is recognised by its own version: `RELEASE_PLATFORMS_SINCE`
beside `RELEASE_PLATFORMS` in `product_identity.py` declares 7.0.0 once, and a release before it predates the set. The
installer already reads only its own archive's and manifest's entries in `SHA256SUMS`, and the hosted predecessor jobs
download only their platform's files. Publication verification follows one owner, `published_release_digests` in
`tools/release/artifact/format.py`: for a predecessor, the signed checksum manifest must name a complete platform
inventory that includes the supported platforms; their assets are verified, and the other platforms' assets and
checksum lines are ignored, unread. Both observers use it, GitHub leaves a predecessor's other assets undownloaded,
and the evaluator compares the supported assets alone. From 7.0.0 on, both observers and the evaluator require exactly
the supported set, GitHub's asset list included. Tests pin both cases, deriving the versions from the declaration.

**D5. Locks.** `uv.lock` and Mise's lock are restricted to the supported platforms by each tool's own environment or
platform setting; the diff removes Windows and Intel macOS entries and changes no kept version, hash or URL. Which
setting does it is measured with the locked tools before the lock is written. Measured on 2026-11-25: uv 0.13.8 refuses
`environments` in `.config/mise/uv.toml` ("only applicable in the context of a project"), so it is `[tool.uv]` in
`pyproject.toml`; `uv lock --offline` then removes colorama, pefile and pywin32-ctypes, the Windows and Intel macOS
wheels and also coverage's four Android wheels, and rewrites two markers to forms equivalent on the supported
platforms. `mise lock --platform` alone changes no entry (dry run: 0 of 50); Mise's `lockfile_platforms` setting makes
`mise lock` remove the 18 `macos-x64` and `windows-x64` rows. A comparison of both locks before and after found no
kept version, hash or URL changed.

**D6. The open Change `terminal-product-convergence`.** Its open tasks 10.2, 12.5 and 13.5 and its four deltas name
Windows; this Change edits them to the supported set in the same commit series, so that archiving either Change cannot
reintroduce a Windows requirement. Its other open work is unchanged. Two of its blocks modified requirements that this
Change removes or rewrites (`ci-diagnostics`' hosted fixtures and bootstrap); this Change carries their current text and
the open Change no longer touches them, so the merged text does not depend on which Change is archived first. Its
Windows sharing-retry requirement keeps its revalidation, permission and journal rules for macOS and Linux.

**D7. Records.** Documents state the supported set once and link to it; decision records that name Windows as current
behavior get a dated amendment line naming this Change and are cited by it; `docs/evidence/windows-real-machine-validation.md`
stays as dated evidence. The release is 7.0.0 with a breaking entry in `CHANGELOG.md`. One record names Windows as
current behavior: DR-0006, which gets the amendment line. The supported set is stated once, in the release policy's
"Supported platforms" section.

**D8. Specifications.** As in aigw-cli's Change of the same name: a Windows-only requirement is REMOVED; a requirement
that names Windows beside the other platforms is MODIFIED with every scenario name kept; a Windows-only scenario leaves
through REMOVED plus ADDED under a new name; requirement text stays within 500 characters.

## Risks / Trade-offs

- GitLab's macOS guest may lack Python 3.12 and 3.13 offline → measured before the job is declared required; until
  then it is not added to the required set.
- The CI paradigm of ETHOS 产品构建 may rename the platform declaration later → the declaration is one place, so a
  later rename touches one file.
- A user on Windows loses the supported program; 6.0.3 stays published.

Telling ETHOS 仓库纳管 that this Change is on `dev` and `main` of both forges follows the publication, so it is the
keeper's follow-up, not a task. The inventory under aigw-cli's `build/evidence/` stays as the evidence this design
cites.
