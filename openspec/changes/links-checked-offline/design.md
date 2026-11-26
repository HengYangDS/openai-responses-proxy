## Context

`tools.quality.governance` runs the repository's one governance graph. Before this Change its link step was
`lychee --config .config/checks/lychee/lychee.toml --offline <tracked Markdown>`, and the `--online-links` option
dropped `--offline`. The local check, `mise run check`, runs Nox's `full` session, whose `governance` session passes
no option, so it checked links offline; GitHub's `source-and-governance` job and GitLab's source job
(`.config/ci/peers/github/source.cue` and `.config/ci/peers/gitlab/pipeline.cue`) passed `--online-links`, so lychee
fetched every external URL. No accepted specification names online link checking; `ci-diagnostics` names documentation
links only among the concerns that the verification graph owns. The ETHOS full proof runs no link check: its two gates
in `.ethos/profile.toml` run Nox's `quality` session and its `coverage` and `tests-3.13`/`tests-3.14` sessions, none of
which runs the governance check.

The coordinating session read the GitLab failures on 2026-11-26: jobs failed on lychee alone on 2026-10-17 (merge
request 99), 10-20, 10-26, 10-28, 11-05 (504, `encode/httpx`), 11-24 (merge request 127, 503) and twice on 11-26 (dev
pipeline 18337, jobs 79738 and 79752: 503 on `github.com/encode/httpcore/blob/1.0.9/docs/network-backends.md` and two
other GitHub pages), while the same URLs answered 200 from the host. aigw-cli checks its links offline only
(`tools/ci/checks.go`, where lychee runs with `--offline`).

## Goals / Non-Goals

**Goals:** one link check, the same in the local check and on both forges, that needs no network and still fails on a
broken local file link or anchor.

**Non-Goals:** checking external URLs by any job; aigw-cli; the product and its release assets.

## Decisions

### D1 Links are checked offline everywhere

The online mode and its option are removed, with no compatibility path; a caller that still passes `--online-links` is
refused by the command parser. Alternatives, each rejected:

- Accepting 503 and 504 as success would hide a real 503 of a linked page.
- More retries or longer waits would only lower the failure rate and slow every job.
- A GitHub token in GitLab CI would widen the credentials that GitLab jobs hold.
- Checking online on GitHub only would break the mirrored overlap of the two forges' checks.

### D2 The lychee configuration declares the mode

`offline = true` moves into `.config/checks/lychee/lychee.toml`, the tool-native owner of lychee's policy in the
responsibility map, and the governance graph passes only the configuration and its inputs. The alternative, keeping
`--offline` in the governance command, was rejected because it leaves part of lychee's policy in Python beside its
configuration.

### D3 Only settings with an offline effect stay

Read in lychee 0.24.2, the release that `.config/mise/config.toml` pins, at tag `lychee-v0.24.2`:

- `lychee-bin/src/client.rs`: offline mode replaces the accepted schemes with `file` alone.
- `lychee-lib/src/filter/mod.rs`: a URI whose scheme is not accepted is excluded before any check.
- `lychee-lib/src/client.rs`: `max_retries` and `require_https` reach only the website checker; `timeout` reaches only
  the HTTP client, the mail checker and the archive lookup of `--suggest`, which the repository does not enable. File
  URIs go to the file checker, which takes none of the three.

`max_retries`, `timeout` and `require_https` therefore have no effect offline and are removed. `include_fragments`
(anchor checks) and `root_dir` (absolute local links) act on file URIs and stay.

Measured on 2026-11-26 with lychee 0.24.2 over the 41 Markdown files tracked at the lane's base, with network access
denied by a macOS `sandbox-exec` profile (`deny network*`; curl under the same profile could not resolve `github.com`):
the previous configuration with `--offline` and the new configuration alone both reported 83 links, 71 local links
passing, 12 external links excluded and no error, exit 0. The previous configuration without `--offline` under the same
profile reported 12 connection errors, exit 2. A file with a missing local file link and a missing anchor failed under
the new configuration with both errors and its external link excluded, exit 2. With this Change's four documents added,
the new configuration reported the same counts over 45 files.

## Risks / Trade-offs

- **A dead or moved external URL is no longer caught by any check.** Before, the forges caught it, together with the
  transient 503 and 504 answers above. A reader who follows the link meets it; a fix replaces the URL as before.
- **The option disappears for any other caller.** The lane holds no other use of `--online-links` than the two forge
  jobs; a caller outside the repository fails loudly in the parser rather than running a different check.
