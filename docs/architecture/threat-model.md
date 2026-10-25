---
subject: openai-responses-proxy:threat-model
role: explanation
state: canonical
relations:
  part_of: README.md
---

# Threat Model

This model covers the installed proxy on one user's workstation: the loopback
listener, its upstream exchanges, the lifecycle commands and their state, and
the signed release assets they install. It records which threats the product
controls, the evidence for each control, and the residual risks it accepts.
The [authority and runtime boundary](authority-and-runtime-boundary.md) owns
the component design; this document owns only the security reading of it.

## Assets

| Asset                | Where it lives                                                                 |
| -------------------- | ------------------------------------------------------------------------------ |
| Client credentials   | The client's `Authorization` header, forwarded upstream for one request.       |
| Conversation content | Request and response bodies in flight; never written to logs or state.         |
| Installed payload    | The current generation, its manifest, journals and native service definitions. |
| Release trust        | The CI-held signing key and the trust anchor that the operator supplies.       |
| Service availability | The listener that the user's coding clients depend on.                         |

The proxy holds no credential of its own. Each request carries the client's
credential, which the proxy forwards to the selected route's upstream and then
discards.

## Trust boundaries

1. **Client to listener.** The listener binds `127.0.0.1` only and does not
   authenticate requests. Every process of the same user, and a web page in a
   local browser, can reach it.
2. **Proxy to upstream.** The release-owned Provider registry or the operator's
   explicit route selects the upstream; request input cannot choose a host.
   TLS verifies against the bundled `certifi` store, and plain HTTP is allowed
   only for a loopback upstream.
3. **Lifecycle to installed state.** Lifecycle commands run as the user and
   write only product-owned roots, whose files belong to the same user.
4. **Release to installation.** Installation admits only a bundle whose signed
   checksum inventory verifies against the operator's trust anchor.

## Threats and controls

| Threat                                                                   | Control                                                                                                                                       | Evidence                                                                            |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| An upstream redirect sends the client's credential to another host.      | The upstream openers refuse every redirect; the client receives the redirect response unfollowed.                                             | `tests/relay/transport/test_upstream_redirects.py`                                  |
| Request input selects an arbitrary upstream host or path.                | Exact Provider routes resolve against the registry; scheme, host and encoded path segments are rejected.                                      | `tests/providers/test_registry.py`, `tests/relay/test_routes.py`                    |
| A web page drains admission or starts a handoff, also via DNS rebinding. | Drain and handoff accept only a loopback client whose `Host` is a loopback address and that sends no `Origin`.                                | `tests/relay/transport/test_admission.py`, `tests/relay/test_admission.py`          |
| A local process stalls a handoff envelope to hold a control handler.     | The handoff envelope is read within 32 KiB and five seconds; a stalled body gets HTTP 408 and its connection closes.                          | `tests/serving/handoff/test_control.py`, `subprocess/test_transition.py`            |
| A web page relays requests through the listener.                         | The listener holds no ambient credential, so a page can only send requests that carry no user credential; browsers block reading the replies. | `relay/downstream.py` header forwarding                                             |
| Credentials or content reach logs.                                       | Logs carry request ids, sizes, statuses and fixed codes; credential-like values are redacted and log files are mode `0600`.                   | `tests/runtime/test_bounded_log.py`, `tests/relay/test_operational_log.py`          |
| A tampered or downgraded release is installed.                           | Signed checksums, manifest and per-member hashes are verified before extraction; a downgrade or same-version reinstall is refused.            | `tests/lifecycle/test_artifact.py`, `tests/lifecycle/transaction/test_admission.py` |
| An interrupted transaction deletes unrelated files.                      | Recovery deletes only exact regular files whose hash and inode it re-checks; journal paths are relative and schema-checked.                   | `tests/lifecycle/transaction/test_disposal.py`                                      |

## Severity and closure

The review on 2026-10-23 found one high-severity gap and one medium gap.

- **High, closed:** the upstream openers used the standard redirect handler,
  which re-sends every request header, including `Authorization`, to whatever
  host and scheme a redirect names. A test reproduced the credential reaching
  a redirect target. The relay now refuses redirects.
- **Medium, closed:** drain and handoff checked only the client address. A web
  page could post a drain from the user's browser and refuse new Responses
  turns for the 30-second lease, repeatedly. Control now requires a loopback
  `Host` and no `Origin`.

## Residual risks

Each residual risk needs another process of the same user, or a change to the
user's own files, which already grants the attacker that user's privileges.
They stay low until that assumption changes.

| Residual risk                                                                                             | Revisit when                                              |
| --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Signature verification resolves `ssh-keygen` from `PATH` and does not pin the release principal.          | The trust anchor may hold more than one principal.        |
| A crafted journal and command snapshot in the data root can make `recover` relink an absolute path.       | Product state may be written by another account.          |
| `recover` reads the listener's `/healthz` without the process check that `status` performs.               | Another account may bind the product port.                |
| The lifecycle lock and state directory use the default `umask`; macOS `watchdog.stderr.log` is unbounded. | A shared host or a long-running watchdog failure appears. |
| Request headers have no read timeout, and Windows binds with `SO_REUSEADDR`.                              | The listener is exposed beyond the loopback interface.    |
| The GitLab release publisher accepts an `http://` API base for its token.                                 | The internal GitLab Forge offers HTTPS.                   |
