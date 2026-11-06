## Why

The owner ruled that publication peers stay independent and that tracked
content must not expose the private GitLab peer: not its address, port,
namespace, project path or numeric project ID. The proxy's changelog and
publication declaration already name no peer, but two release tests still use
the proxy's real GitLab project ID as a fixture. A history rebuild, prepared
with ETHOS, removes the identity from history once the tip holds none of it.

## What Changes

- The GitLab publication tests use the fixture project ID 101 instead of the
  proxy's real project ID.
- `repository-organization` gains a requirement that tracked content holds no
  peer coordinates and never the private peer's identity.

**Non-goals:** the history rebuild, and old Release assets, each with its own
owner and step.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `repository-organization`: tracked content holds no peer coordinates.

## Impact

Two test files change, and no product behavior does.
