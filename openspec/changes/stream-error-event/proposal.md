## Why

A Responses stream can end with a top-level `error` event, which the Responses streaming reference defines
(`type`, `code`, `message`, `param`). The proxy knows three terminal events, `response.completed`,
`response.failed` and `response.incomplete`. When an upstream sends `error` and closes, the proxy forwards the
event, then treats the upstream's end as a truncated stream: it closes the client's connection without ending
the chunked body, and logs `sse_end_without_terminal … reason=unknown`.

Observed on 2026-11-14 on the `dmxapi` route: the provider refused a request that carried a web search tool with
an `error` event of code `validation_error`. Codex reported "error decoding response body", took the refusal for a
disconnection and sent the same request five more times. The proxy's log gave no hint, because it names no event
of a stream that ends early.

## What Changes

- A top-level `error` event ends the stream. The client receives the event unchanged and a complete HTTP body,
  and the request is not sent again.
- The log names the ending as `terminal=error` with the event's code, when the code is a short token.
- A stream that ends without any terminal event names the type of its last event in the log.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `provider-portable-responses`: a top-level `error` event is a stream ending, and an early ending is diagnosable
  without content.

## Impact

`relay/sse.py`, one helper in `protocol/response.py` and their tests. The bytes that a client receives for such a
stream are the same events as before; what changes is that the HTTP body ends instead of the connection breaking.
What a client does with an `error` event stays the client's. Out of scope: retrying after an `error` event, and
what the three other terminal events do.
