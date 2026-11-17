## Why

Since release 3.1.2 an installation keeps its payload in immutable generations under a stable control root, and
one selector names the active generation and its predecessor. Release 4.0.0 stopped migrating a payload that
lies at the control root itself, and no release under this product's name has written one. The readers of such
a payload are still here. A runtime carrier and an executable at the control root are accepted locations;
`status` and `rollback` skip the binding of the installed record to the selection when there is no selector;
`uninstall --purge` removes a root-level payload by its manifest; a serving process whose payload lies outside
the generation store takes its own folder for the control root.

The lifecycle's one context type also carries an executable path at the control root whenever nothing is
selected. No file is there, and every command still probes it: for a runtime carrier, for a manifest, against
the running processes, the native service and the command. The main `runtime-upgrade` specification still
describes an upgrade from an installation without generations, which no release under this product's name has
performed.

This is task 5.8 of `terminal-product-convergence`.

## What Changes

- An installed payload is read only through the generation that the selector names. A control root that holds
  payload files at its own level, with no selector and no transaction, holds unverified content: `status` names
  no payload there, and `uninstall --purge` preserves every byte and fails, as the specification already says
  of content without ownership evidence. Before, a root-level payload with a valid manifest was reported as an
  installation and purged.
- The lifecycle has two contexts: the control root, which names no payload, and one payload generation. A
  function that reads a payload takes the second, and a command that can run without a selection says what it
  does without one.
- A first installation's command record names its candidate generation's executable. Before, it named the
  root-level path. The record keeps its form.
- The platform of an installation comes from the host and no longer from an executable's name.
- The main specification loses the scenario "The retained-generation transition is introduced". Its
  requirement returns under a new title with its statement and its other scenarios unchanged, because OpenSpec
  has no operation that removes one scenario.

No command, option, route, field of `status --json` or exit code changes for an installation that a release of
this product wrote. For a control root without a selection, the text of `payload_integrity.detail` and of a
degraded state's `detail` changes.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `runtime-upgrade`: a payload is read only through a selected generation; the scenario of the transition from
  an installation without generations is gone.

## Impact

- `lifecycle/context.py` and `lifecycle/generation.py`: the two contexts and their producers.
- `lifecycle/control.py`, `uninstall.py`, `install.py`, `rollback.py`, `transaction/execution.py`,
  `transaction/completion.py`, `transaction/recovery.py`, `runtime_spec.py`, `supervision/macos.py`,
  `serving/identity.py`: the functions that were reachable without a selection, and the removed locations.
- `tools/release/verification.py` and the test fixtures: every test context names a payload in a generation.
- `CHANGELOG.md` and `VERSION`: release 6.0.3.

Out of scope: the transaction journal's and the command record's form; the selector's form; the handoff
protocol; what a release older than 6.0.0 left behind, which 6.0.0 already refuses.
