## Why

A Responses stream can end with a top-level `error` event, which the Responses streaming reference defines
(`type`, `code`, `message`, `param`). The proxy knows three terminal events, `response.completed`,
`response.failed` and `response.incomplete`. When an upstream sends `error` and closes, the proxy forwards the
event, then treats the upstream's end as a truncated stream: it closes the client's connection without ending
the chunked body, and logs `sse_end_without_terminal … reason=unknown`.

That report is false twice. The upstream ended its stream on purpose, so the client is told of a network fault
that did not happen; and the log names no event of a stream that ends early, so the cause cannot be read from it.
Observed on 2026-11-14 on the `dmxapi` route: the provider refused a request that carried a web search tool with
an `error` event of code `validation_error`. Codex reported "error decoding response body", and finding the cause
took a trace of the client.

## What Changes

- A top-level `error` event ends the stream. The client receives the event unchanged and a complete HTTP body,
  and the request is not sent again. Events that an upstream sends after it are not forwarded.
- The log names the ending as `terminal=error` with the event's code, when the code is a short token.
- A stream that ends without any terminal event names the type of the last event that the proxy accepted.
- `status --json` reports such an ending as the failure class `stream_error`; it was `stream_eof`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `provider-portable-responses`: a top-level `error` event is a stream ending, and an early ending is diagnosable
  without content.

## Impact

`relay/sse.py`, two helpers in `protocol/response.py` and their tests. The events that a client receives for such
a stream are the same as before; what changes is that the HTTP body ends instead of the connection breaking.

Not addressed: what a client does with an `error` event. Codex 0.169.0 does not read it: against a scripted
upstream it sent the request six times with either ending, and only its message changed, from "error decoding
response body" to "stream closed before response.completed". Also out of scope: retrying after an `error` event,
an event named `error` only in its `event:` line, and what the three other terminal events do.
