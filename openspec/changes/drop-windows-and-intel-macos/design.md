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
Change.

**D2. One platform declaration for CI.** A provider-neutral declaration in `.config/ci/graph/` names each platform's
runner per forge: GitHub `macos-latest` and `ubuntu-24.04`; GitLab `macos-latest` with `review` for merge requests and
`protected` for `dev`, `main` and tags, and the existing Linux runner variables. The GitHub adapters stop writing
`macos-26` literals. GitLab gains the macOS Python verification job that GitHub runs today (3.12, 3.13, 3.14), rendered
from the same job declaration, with its own default-image and default-tag override and a Mise bootstrap instead of
`apt-get`; its interpreters come from Mise or `uv python install` as measured on the guest before the job is declared
required. Native assets, the release lifecycle and assembly stay on GitHub, where they run today; they are not an
overlap.

**D3. Code.** `lifecycle/supervision/windows.py` (Task Scheduler) and its tests leave; the supervision adapter choice is
launchd or systemd. Every `sys.platform`/`os.name` Windows branch, `InstallationContext.windows` and the `windows=`
parameters, the `.cmd` launcher, `LOCALAPPDATA` defaults, `ntpath`/`PureWindowsPath`, the handoff `socket.share` path
and its `listener_share_b64` field, the watchdog's detach flags, the kernel32 probe, `winerror` retries and the Windows
`SO_LINGER` packing leave. A seam with one remaining implementation collapses into it. Archive validation that refuses
Windows-style member names stays: it guards untrusted input, whatever host reads it.

**D4. Published predecessors.** Releases up to 6.0.3 carry a Windows asset. A command that verifies such a release
(the published-release compatibility check and any upgrade from it) verifies the platforms the current product
declares and ignores assets outside them without treating them as an error; the evaluator's "exactly the declared set"
rule applies to releases built from this Change on.

**D5. Locks.** `uv.lock` and Mise's lock are restricted to the supported platforms by each tool's own environment or
platform setting; the diff removes Windows and Intel macOS entries and changes no kept version, hash or URL. Which
setting does it is measured with the locked tools before the lock is written.

**D6. The open Change `terminal-product-convergence`.** Its open tasks 10.2, 12.5 and 13.5 and its four deltas name
Windows; this Change edits them to the supported set in the same commit series, so that archiving either Change cannot
reintroduce a Windows requirement. Its other open work is unchanged.

**D7. Records.** Documents state the supported set once and link to it; decision records that name Windows as current
behavior get a dated amendment line naming this Change and are cited by it; `docs/evidence/windows-real-machine-validation.md`
stays as dated evidence. The release is 7.0.0 with a breaking entry in `CHANGELOG.md`.

**D8. Specifications.** As in aigw-cli's Change of the same name: a Windows-only requirement is REMOVED; a requirement
that names Windows beside the other platforms is MODIFIED with every scenario name kept; a Windows-only scenario leaves
through REMOVED plus ADDED under a new name; requirement text stays within 500 characters.

## Risks / Trade-offs

- GitLab's macOS guest may lack Python 3.12 and 3.13 offline → measured before the job is declared required; until
  then it is not added to the required set.
- The CI paradigm of ETHOS 产品构建 may rename the platform declaration later → the declaration is one place, so a
  later rename touches one file.
- A user on Windows loses the supported program; 6.0.3 stays published.
