# Release 8.0.1 with OpenSpec 2.2.0

## Context

`VERSION` is 8.0.1 and `CHANGELOG.md` dates `## [8.0.1]` 2026-11-28. The OpenSpec 2.2.0 entry sits under
Unreleased, above it. `tools/release/metadata.py` refuses a pending release dated after the current UTC day.

## Goals / Non-Goals

The goal and non-goals are the proposal's.

## Decisions

### D1. Move the entry and keep the date

The entry moves unchanged to the end of `### Changed` under `## [8.0.1]`, next to the ETHOS pin, which is the other
change to development tooling in this release. The date stays 2026-11-28: the release is tagged before the UTC day
ends, and the metadata check refuses a later date.

Alternative: release 8.0.1 from the commit before OpenSpec landed and leave the entry for the next release. The owner
chose otherwise. That path would also have to tag a commit behind `main`.

## Risks / Trade-offs

- **The tag misses the UTC day of its date** if publication is delayed past 2026-11-28 in UTC (08:00 on 2026-11-29 in
  China Standard Time). The heading then carries the day the release was prepared, not the day it was tagged.
