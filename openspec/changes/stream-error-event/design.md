## Context

`relay/sse.py` reads one upstream stream event by event. It holds `response.created` and `response.in_progress`
back until content arrives, so that an explicit failure before content can be reopened, and it stops reading at a
terminal event. A stream that ends without one is a truncated stream: after the response headers went out, the
only truthful signal is to break the connection.

## Goals / Non-Goals

**Goals:**

- A stream that the upstream ended on purpose, with an `error` event, reaches the client as a complete response.
- An operator or an agent that reads the log can tell an `error` ending from a truncation, and can tell where a
  truncated stream stopped.

**Non-Goals:**

- Classifying `error` codes, or retrying after one. The upstream answered; the at-most-once rule keeps the request
  from being sent again.
- Changing the event's bytes or translating it into `response.failed`.
- Making a client stop resending. Codex 0.169.0 ignores the event (see Risks), and a translation into
  `response.failed` would not help it either: it resends after a failure whose code it does not know.
- Another spelling of the event: one that is named `error` only in its `event:` line, with data of no type,
  stays an event like any other.

## Decisions

### `error` joins the terminal events

The reader stops at an `error` event as it does at the three others, and the relay then ends the chunked body.
The event is not held back: it is content for the client, so the response headers go out with it and no reopen
can follow. A pre-content `response.failed` with `server_error` is still the only ending that is reopened.

Alternative considered: keep reading after `error` and wait for the upstream's close. Rejected: an upstream that
keeps its connection open after its last event would hold the client until the deadline, the case that the
existing terminal handling already avoids.

### The log carries a code token, never the message

The `error` event's `code` appears in the log only when it is a token of lowercase letters, digits and
underscores, at most 64 characters; anything else is `unknown`. The message, which can quote request content,
never appears. Telemetry records the failure class `stream_error`.

The code is read beside the event's type, where the Responses reference puts it, and otherwise in an `error`
member, where Codex 0.169.0's reader looks for it. A code beside the type that is not a token is `unknown`; the
nested one is not a second chance for it.

This is wider than `failure_diagnostic`, which names three known codes of `response.failed`. A provider's `error`
codes are an open vocabulary, and a closed list would name almost none of them on a third-party route, which is
where the event was met. The grammar admits no sentence: no space, capital or punctuation. A provider that put a
secret into `code` in that shape would see it logged; no known provider does.

The log's own redaction applies to this line as to every line: it replaces a word that reads as a credential
field, such as `authorization_error` or `api_key_invalid`, with `[redacted]`, together with the word after it.
The code and the event type therefore stand last in their lines, so that no other field goes with them.

### An early ending names its last event type

`sse_end_without_terminal` gains `last=`: the type of the last event that the proxy accepted, when it is a token
of lowercase letters, digits, dots and underscores, at most 64 characters; `untyped` for an event without a type,
such as a comment or `[DONE]`; `other` for a type outside that grammar; `none` for a stream without an accepted
event. An event that the proxy rejects, such as malformed JSON, is not the last accepted one: the line then names
the event before it, beside `detail=projection_failed`. Event types are protocol vocabulary, not content.

## Risks / Trade-offs

- **A client relied on the broken connection to retry** → it now sees a complete body with an `error` event and
  decides by the event, which is what the protocol defines.
- **Codex resends as before** → measured on 2026-11-14 with Codex 0.169.0 against a scripted upstream that
  answers `response.created`, `response.in_progress` and the `error` event: six requests when the body ends
  completely, six when the connection breaks. Its message is "stream closed before response.completed" in the
  first case and "error decoding response body" in the second. The resends are the client's to fix.
- **An upstream sends `error` and then more events** → they are not read, as with the other terminal events.
