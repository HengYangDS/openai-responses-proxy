## ADDED Requirements

### Requirement: A top-level error event ends the stream

A top-level `error` event of a Responses stream SHALL end the stream like a
terminal response event. The client SHALL receive the event unchanged and a
complete HTTP body, and the request SHALL NOT be sent again. Diagnostics SHALL
name the ending without the event's message.

#### Scenario: An upstream refuses a request inside the stream

- **WHEN** an upstream sends `response.created`, `response.in_progress` and a
  top-level `error` event, then closes
- **THEN** the client receives those events unchanged, and the chunked body ends
- **AND** the proxy does not reopen the request
- **AND** the log names `terminal=error` and the event's code when the code is a
  token of lowercase letters, digits and underscores of at most 64 characters,
  `unknown` otherwise, and never the event's message.

#### Scenario: A stream ends without a terminal event

- **WHEN** an upstream stream ends without a terminal event
- **THEN** a response whose headers were sent is closed without the end of its
  chunked body
- **AND** the log names the type of the last event when it is a token of
  lowercase letters, digits, dots and underscores of at most 64 characters,
  `untyped` for an event without a type, `other` for another type and `none`
  for a stream without events.
