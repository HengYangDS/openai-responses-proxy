## Context

`lifecycle/command.py` projects `openai-responses-proxy.cmd` on Windows and a
symbolic link elsewhere, and `status` classifies the launcher as owned only
when its bytes equal the canonical launcher for the target.

## Goals / Non-Goals

**Goals:**

- The specification and the installer describe the same Windows command.

**Non-Goals:**

- Changing the launcher or adding another command form.

## Decisions

### Restate the requirement rather than add a second one

The source-side upgrade requirement already owns the command form, so the
correction modifies it in place and keeps every scenario name.

## Risks / Trade-offs

- None beyond the wording; the behavior has shipped since v4.0.6.
