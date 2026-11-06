## Why

The runtime-upgrade specification says the Windows user command is a hard link
and that no wrapper is required. Since v4.0.6 the installer has projected a
`.cmd` launcher instead and classified its ownership by the launcher's exact
bytes; no release created a hard-linked command. The specification should
describe the command that ships.

## What Changes

- The source-side upgrade requirement names a symbolic link on POSIX and a
  `.cmd` launcher on Windows as the two command forms.
- The Windows scenario requires the launcher's exact bytes, ownership by those
  bytes and no symbolic-link privilege or copied executable.

Non-goals: installer behavior does not change; the unused hard-link kind was
removed separately under `terminal-product-convergence`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `runtime-upgrade`: the Windows command form is a `.cmd` launcher.

## Impact

Specification only. Local proof covers it; no release, installation or
runtime acceptance follows from it.
