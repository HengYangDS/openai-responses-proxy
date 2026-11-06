## Context

ETHOS resolves each publication peer from the clone's Git configuration, and the
proxy's changelog and publication declaration already name no peer. Only two
release tests carry the proxy's real GitLab project ID, on a fixture host.

## Goals / Non-Goals

**Goals:**

- No tracked content names the private GitLab peer's host, port, namespace,
  project path or project ID.

**Non-Goals:**

- Rewriting history, which the ETHOS rebuild does.

## Decisions

### A fixture project ID

The tests need a GitLab project ID to exercise publication requests, not the
real one. They use 101, the fixture ID that aigw-cli's tests use too.

## Risks / Trade-offs

- None for behavior: only test inputs change.
