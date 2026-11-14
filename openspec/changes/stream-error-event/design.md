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

### An early ending names its last event type

`sse_end_without_terminal` gains `last=`: the type of the last event when it is a token of lowercase letters,
digits, dots and underscores, at most 64 characters; `untyped` for an event without a type, such as a comment or
`[DONE]`; `other` for a type outside that grammar; `none` for a stream without events. Event types are protocol
vocabulary, not content.

## Risks / Trade-offs

- **A client relied on the broken connection to retry** → it now sees a complete body with an `error` event and
  decides by the event, which is what the protocol defines. Codex's own handling was not changed or measured
  beyond the observed retries.
- **An upstream sends `error` and then more events** → they are not read, as with the other terminal events.
