---
subject: openai-responses-proxy:decision-0004-local-first-independent-forge-release
role: decision
decision_id: DR-0004
decision_status: accepted
state: canonical
relations:
  part_of: README.md
---

# DR-0004: Publish One Signed Product Bundle through Independent Forges

- Date: 2026-08-07

## Context

GitLab and GitHub are separate transport, account, CI, Release, and asset
domains. Creating different Git histories or tag objects for them destroys exact
product identity. Making either workflow query, authenticate to, wait for, or
publish through the other creates a common failure path and prevents local closure.

## Decision

A clean accepted source tree can build, verify, install, exercise, and uninstall
without a Forge. The product commit and annotated release tag are created and
signed once in local Git. The admitted native builder for each supported
platform produces that platform's exact asset pair. One product assembler
verifies the complete platform inventory, creates one checksum manifest, and
signs the resulting bundle once.

GitLab and GitHub remain optional publication peers with the same semantic
role. Each authenticates its own transport and verifies the same Git objects,
but neither may build assets, subset, repackage, or re-sign the product bundle.
A publication adapter only transfers and re-downloads the immutable bundle.
Physical runner placement does not make either peer a source of product
identity.

Cross-Forge comparison is read-only and occurs only after both publications
exist. It requires exact branch and tag object OIDs, the complete asset inventory,
byte-identical checksums and signature, and the same product trust-anchor digest.

## Alternatives Rejected

- **Forge-specific histories, tags or signatures:** rejected because the
  publication destination must not redefine product identity.
- **A peer waiting for or publishing through another peer:** rejected because
  either peer must remain useful during the other's outage.
- **Independent rebuilding or repackaging by publication adapters:** rejected
  because separately produced bytes are not the one admitted product bundle.

## Consequences and Boundary

One Forge can remain usable during an outage of the other. One-sided success is
reported as one-sided publication, never as a dual release. Each platform asset
is admitted once into one complete bundle, and that bundle is assembled and
signed once. Forge independence concerns publication and availability, not
duplicate construction or signing. Push credentials may differ, but they never
change Git objects. Product source contains no personal private key, credential,
or checkout path.

## Evidence and Revisit

The [release governance specification](../../openspec/specs/release-governance/spec.md)
owns object and asset acceptance; [Forge operations](../operations/forge-operations.md)
describes independent publication. Their existence is not proof of any current
remote state.

Revisit if one publication plane is retired or an external attestation authority
replaces local Git object signatures. Multiple organizational endorsements should
remain detached attestations over one product SHA, not alternate Git histories.
