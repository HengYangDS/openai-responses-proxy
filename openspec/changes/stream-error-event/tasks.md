## 1. The stream ending

- [ ] 1.1 Treat a top-level `error` event as a stream ending: the client receives the event unchanged and the end
      of the chunked body, and no reopen follows; tests cover the event before content, after held events and
      after committed content, and a client that reads the whole response over a real connection.
- [ ] 1.2 Log the ending as `terminal=error` with a bounded code token, and never the event's message; a test
      shows a message and an out-of-grammar code absent from the log.
- [ ] 1.3 Name the last event type of a stream that ends without a terminal event; a test covers a typed event,
      an untyped one, a type outside the grammar and a stream without events.

## 2. Verification

- [ ] 2.1 `mise run check` passes on the lane head.
- [ ] 2.2 The hosted graph passes at the proposal head, and a fresh-context review of the lane is read.
