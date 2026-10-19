---
subject: openai-responses-proxy:windows-real-machine-validation-2026-07-23
role: evidence
state: archived
relations:
  part_of: README.md
---

# Windows Real-Machine Validation

Status: dated proof (2026-07-23).

Before this record the Windows adapter was covered only by research and unit
tests; Windows containers need a Windows host, so no end-to-end run had occurred
on a real machine. This is the first such run.

- **Scope:** the `fix/windows-watchdog-lifecycle` payload, installed and
  uninstalled through its own entry points on a live Windows host.
- **Verifier:** manual operation on the host plus `schtasks`, Win32 process
  ownership, token elevation (`GetTokenInformation`), and TCP listener queries.
- **Limit:** proves current install/lifecycle behavior on one host and build. It
  is not a claim about any historical Codex conversation, and it does not lower
  the authority of source, unit tests, `VERSION`, or CI.

## Host

- Windows 10 Enterprise, build 19045.
- Per-user CPython 3.14; `py -3` resolved the real interpreter and avoided the
  0-byte WindowsApps Store stub.
- Two contexts exercised: the interactive account, and a purpose-created
  standard user that is **not** a member of Administrators.

## Lifecycle fixes verified

Each fix in this branch was reproduced against its original real-host failure.

| Fix                          | Original real-host failure                                                                                                                                                                   | Evidence after fix                                                                                                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Watchdog self-heal           | Killing the watchdog left it absent for 180 s (3× the interval); `RestartOnFailure` reacts only to a failed task _launch_, and a `LogonTrigger` `<Repetition>` only arms at an actual logon. | With the repeating `<TimeTrigger>` (past `StartBoundary` + `PT1M`) and `MultipleInstancesPolicy=IgnoreNew`, a killed watchdog relaunched in ~32 s; repeated fires while alive were no-ops. |
| Uninstall stops the watchdog | `schtasks /delete` removed only the definition; the surviving watchdog respawned the proxy in the same second.                                                                               | Uninstall now terminates the watchdog matched to this install's own launcher/script paths; after uninstall the watchdog and proxy were gone and nothing respawned over a full interval.    |
| Windowless run               | The `cmd.exe /c` wrapper held a visible console for the whole watchdog lifetime.                                                                                                             | The task runs a generated `.pyw` via `pythonw.exe`; the watchdog and proxy report `MainWindowHandle=0` and no `cmd`/`conhost` is allocated for them.                                       |

## Standard-user interactive logon

Two properties can only be observed under a real interactive logon of a standard
user; they were confirmed here from a separate elevated session after a
fast-user-switch logon of the standard account.

| Property                                        | Evidence                                                                                                                                             |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| (a) Auto-start at logon under the standard user | On logon the watchdog `pythonw` appeared owned by the standard user, and the proxy was spawned as its direct child, also owned by the standard user. |
| (b) Non-elevated least-privilege token          | Both the watchdog and proxy reported `TokenIsElevated=0` with `ElevationType=1` (Default; no split token).                                           |
| Consumer endpoint                               | The standard user's client independently pointed at the loopback listener; the proxy did not write that configuration.                               |
| Listener                                        | The proxy owned the loopback listener on the configured port.                                                                                        |
| Idempotent self-heal                            | While the watchdog was alive, the task's last result was the `IgnoreNew` duplicate-instance rejection (`0x800710E0`), i.e. a re-fire is a no-op.     |

### Note on elevation type

`ElevationType=1` here means an ordinary standard-user token with no UAC split,
which is the intended result for `InteractiveToken` + `LeastPrivilege`. The
built-in Administrator account instead reports `TokenIsElevated=1` because that
account has no filtered token to hand back; that is a property of the account,
not a defect in the task principal. The standard-user run is the authoritative
check for non-elevated operation.

## Current boundary

This record proves only the Windows 10 lifecycle run performed on 2026-07-23.
Current cross-platform source acceptance is governed separately by the hosted
Windows Python 3.12, 3.13, and 3.14 matrix; that matrix does not retroactively
extend this dated real-host proof.
