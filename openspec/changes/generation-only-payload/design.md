## Context

`lifecycle/context.py` has one type, `RuntimeContext`, whose `executable` is required. `context.create` fills it
with `install_dir/bin/<name>`, and `lifecycle/generation.py` replaces that with a generation's executable when a
selector exists. Without a selector the root-level path travels through every command. The proposal names what
still accepts a payload there. This design decides what a control root without a selection is, how the model
says it, and what the three commands that run without a selection do.

Paths below are relative to `src/openai_responses_proxy/`.

## Goals / Non-Goals

**Goals:**

- No source or test reads or builds an installed payload outside a generation.
- A function that needs a payload cannot be given a context without one.
- The states without a selector that a release can produce keep their behavior: nothing installed, a first
  installation in progress, its rollback or recovery, and an interrupted purge.

**Non-Goals:**

- A refusal of a root-level payload in every command.
- A change of the journal, of the command record, of the selector or of the handoff protocol.
- Reading what a release older than 6.0.0 left behind.

## Decisions

### A control root without a selection holds no payload

- **Rests on:** what a control root can be, decided by the evidence on disk in this order:

  | Transaction journal | Selector | Control root                     | Read as                                       |
  | ------------------- | -------- | -------------------------------- | --------------------------------------------- |
  | present             | any      | any                              | a transaction: the commands ask for `recover` |
  | absent              | valid    | any                              | an installation: its selected generations     |
  | absent              | invalid  | any                              | invalid                                       |
  | absent              | absent   | absent                           | not installed                                 |
  | absent              | absent   | holds a generation store         | invalid: a store without a selector           |
  | absent              | absent   | holds anything else, or is empty | no payload: unverified content                |

  The first five rows are today's behavior and stay. The last row is today's behavior for an empty control
  root: `status` is `degraded`, `install` refuses a root that is not empty
  (`transaction/execution.py`, "installed payload root contains unverified content"), `rollback` has nothing
  retained, and `uninstall --purge` fails. Today a root-level payload with a valid manifest and an installed
  record leaves that row and is read as an installation.

  Who can have written such a payload: the selector arrived in 3.1.2 (`ed77bb4f`); 4.0.0 removed the migration
  of a payload without generations; 5.0.0 renamed the product, and the default roots derive from the product's
  name; every successful installation selects its generation (`transaction/execution.py`, `generation.select`
  in `activate`). So no release under this name wrote one. The states without a selector that a release does
  produce, other than "not installed", all carry a journal: a first installation before its activation, its
  rollback and recovery, and a purge after it deleted the selector.

- **Considered:** refuse a root-level payload in each reader; refuse it once in `context.create`; read it as
  the unverified content of the table's last row.
- **Chosen because:** a refusal in each reader keeps every reader. A refusal in `context.create` stops
  `status` and `uninstall` as well, which then cannot report or remove what the control root itself proves.
  The last row needs no new refusal: `status`, `install`, `rollback` and `reload` already answer it, and the
  main specification already says that content without ownership evidence is preserved and that purge fails
  closed. `uninstall --purge` answers it today through the root-level manifest that it does not find. Without
  that look it fails in a sentence of its own, before it writes a journal: a journal that named the installed
  record alone would remove the record and only then stop at the content that it does not know. The purge's
  cases, by what the control root holds:

  | Generations in the store | Installed record | Selector | Purge                                                     |
  | ------------------------ | ---------------- | -------- | --------------------------------------------------------- |
  | none                     | any              | any      | refused: no payload generation that this product owns     |
  | some                     | present          | any      | removes them; the record names the release                |
  | some                     | absent           | present  | removes them; the selected generation's manifest names it |
  | some                     | absent           | absent   | refused: neither a selector nor an installed record       |

  The third row is today's behavior and stays: a lost installed record does not keep an operator from removing
  a selected payload.

- **Applies to:** `status`, `doctor`, `install`, `rollback`, `reload`, `uninstall`, `recover`; the accepted
  locations in `runtime_spec.py`, `serving/identity.py` and `supervision/macos.py`; the clauses
  `selection is None or` in `control.py`; `transaction/completion.py` `purge`; `tools/release/verification.py`.

### Two contexts: the control root, and one payload generation

- **Rests on:** a reading of every function that receives a context (68 reads of `executable` in 21 source
  files). The functions that read the payload location and can be reached without a selector, with what the
  root-level path does there today:

  | Function                                                                    | Without a selector today                                                                      |
  | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
  | `context.create`                                                            | looks for a carrier beside the root-level path                                                |
  | `control.status`                                                            | reads a root-level manifest; compares the path with the listener, the service and the command |
  | `control.reload`                                                            | through `status`, or reads the root-level manifest                                            |
  | `uninstall.uninstall_product`, `_stop_proxy`                                | compares the command with the path; adds the path as one more process identity                |
  | `transaction/execution.commit_projection`                                   | writes the path into the command record of a first installation                               |
  | `transaction/completion.admit_prior_projection`, `restore_prior_projection` | compare the command with the path                                                             |
  | `transaction/completion.purge`                                              | hashes a root-level payload                                                                   |
  | `generation.context`                                                        | takes the platform from the path's name; looks for a root-level `.exe`                        |
  | the Linux and Windows adapters' `uninstall`                                 | look for a watchdog started from the path                                                     |

  Every other reader of `executable` is reached only with a generation: behind a selector that was read, with
  the candidate of a transaction, or in the watchdog, whose context names its own program.

- **Considered:** one type whose `executable` may be absent; one type that keeps the root-level path and whose
  readers stop reading it; one type whose path without a selection is the running program's own; two types,
  where the payload context is a control-root context with an executable.
- **Chosen because:** with an optional field every reader narrows it, also the many that are only ever given a
  generation. A path that readers agree not to read is the present state, and the next reader can read it
  again. The running program's path can match: the installed command's program is the active generation's
  executable. With two types the signature says which function needs a payload, the strict type check refuses
  a read of `executable` on a control-root context, and each function of the table has to say what it does
  without a payload. A payload context is a control-root context, so a function that needs the control root
  alone takes either.
- **Applies to:** `context.py` (the types; `create` returns the control root's context, with the settings of
  the selected generation's carrier when there is one); `generation.py` (`selected_context` and
  `control_context` return a payload context or nothing; `context` and `owned_contexts` return payload
  contexts); the functions of the table; the adapters, whose `install`, rendering and process functions take a
  payload context and whose `status` and `uninstall` take the control root's.

### The platform comes from the host

- **Rests on:** `generation.context` decides Windows by the `.exe` suffix of the context's executable and, for
  another name, by whether `install_dir/bin/<name>.exe` exists. In the product that name is the default that
  `context.create` chose by the host (`config.os.name`), or the watchdog's own program: the platform already
  comes from the host, by way of a name. Only a test gives a context the name of another platform. An artifact
  is checked for the consistency of its own members (`candidate.validate`), not against the host, and this
  design adds no such check.
- **Considered:** read the platform from each generation's manifest; keep the name; one field of the control
  root's context, set from the host when the context is created.
- **Chosen because:** one decision in one place, and no look at the control root's own level. A test that
  models another platform sets the field, where it now gives the executable another name.
- **Applies to:** `context.create`, `generation.context`, the fixture `platform_context`.

### A first installation's command record names its candidate

- **Rests on:** the command record (`command.json`, schema 2) requires an absolute `target` and uses it only
  when its state is `owned`. Its state for a first installation is `absent`: a command path that another
  program owns is refused before. Today the target of a first installation is the root-level path. The
  restoration of a first installation compares the command with that path once more, after the candidate's
  command has been detached and the selector cleared; the comparison can only find the command absent, or owned
  by another program.
- **Considered:** a third schema with an optional target; the candidate generation's executable as the target;
  for the restoration, the same comparison against the candidate's executable, or one function that requires
  the command to be absent.
- **Chosen because:** a new schema needs a second reader for a journal that 6.0.2 left in the middle of a
  first installation. The candidate's executable is absolute, lies in a generation, and is what the transaction
  makes the command name. A record of 6.0.2 with the root-level target and the state `absent` reads as before,
  because the target of an `absent` record is not used. Without a previous selection the restoration asks one
  thing, that the command is absent, and a function with that name says it.
- **Applies to:** `transaction/execution.commit_projection`, `transaction/completion.admit_prior_projection` and
  `restore_prior_projection`, `transaction/recovery`, `command.py`.

### Without a payload, `status` and `uninstall` act on what the control root proves

- **Rests on:** what identifies each owned thing. The native service is named by the control root
  (`context.service_id`). A listener or a watchdog is ours when its program is a generation's executable. The
  command is ours when it resolves to the control executable. Without a payload the last two have nothing to
  compare with: today they are compared with the root-level path, which matches nothing.
- **Considered:** keep a comparison with a path that cannot match; say the result directly.
- **Chosen because:** the result is the same and the code says why: without a payload no listener is verified,
  a command path that exists is another program's, and the payload's integrity is "no payload generation is
  selected". `uninstall` deregisters the service of the control root, ends the processes of the generations
  that the store still holds (an interrupted purge), and removes no command that it cannot prove.
- **Applies to:** `control.status`, `control.reload`, `uninstall.uninstall_product`, `_stop_proxy`, the Linux
  and Windows adapters' `uninstall`.

### The release is 6.0.3

- **Rests on:** the first decision: no release under this name wrote the state whose handling changes. For an
  installation that a release wrote, no command, option, route, field of `status --json` or exit code changes.
  For a control root without a selection the text of `payload_integrity.detail` changes.
- **Considered:** a minor or a major version for the purge of a root-level payload, which a test held.
- **Chosen because:** the version follows what an operator of a released installation can observe, and that
  is nothing. The changelog names the removal under its own heading, so that it is not read as a fix.
- **Applies to:** `VERSION`, `CHANGELOG.md`.

## Risks / Trade-offs

- **A journal that 6.0.2 wrote.** A first installation interrupted under 6.0.2 leaves a command record with the
  root-level target and the state `absent`; this release reads it (see the fourth decision). A test builds that
  record by hand.
- **An interrupted purge.** It has generations and no selector, with a journal. Its re-run must end the
  processes of the generations that are left and must not need a selector; the active Change's delta says so
  ("Payload removal is interrupted"). A test holds it across this change.
- **The width of the change.** Every signature that takes a context is read again. The suites and the strict
  type check carry it locally; the journeys from the published 6.0.2 on macOS, Linux and Windows, which only
  the hosted graph runs, carry the upgrade, the retained predecessor and the rollback.
- **Windows and Linux adapters.** Their `uninstall` finds a watchdog by its program. Whether deleting the
  Windows task ends its watchdog was not established; the adapter keeps looking for the watchdogs of the
  generations that the store holds.
- **The order of `uninstall`.** It reads the generation store before it deregisters the service, because the
  adapters' `uninstall` needs the generations' executables to find their watchdogs. An invalid store is now
  refused before any effect; before, it was refused after the service had been removed. On Linux and Windows
  the adapters look for the watchdogs of every generation of the store, where they looked for the active one's.
- **Two refusals that read no payload.** `install` of a first installation while a runtime answers on the port,
  and `install` of the installed release without a selection, raise the sentences that their branches already
  raise ("installed listener process generation is not verified", "requested artifact is not the verified
  active installation"); before, the second ended in a carrier read at the control root.
- **The release tool's custody file** holds the control root's fields. A test process whose generation folder
  is gone from the store is found only through the service's configured executable.
- **Not measured here:** nothing of this design rests on platform timing or on another program's behavior.

## Migration Plan

None for an operator. The upgrade from 6.0.2 is an upgrade between two installations with a selector. The
rollback is `openai-responses-proxy rollback --to-release 6.0.2`.
