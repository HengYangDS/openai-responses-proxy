## ADDED Requirements

### Requirement: A top-level error event ends the stream

A top-level `error` event of a Responses stream SHALL be a terminal event: it
SHALL end the stream as `response.completed`, `response.failed` and
`response.incomplete` do, and it SHALL commit the response as content. The
client SHALL receive the event unchanged and a complete HTTP body, and the
request SHALL NOT be sent again. Diagnostics SHALL name the ending without the
event's message.

#### Scenario: An upstream refuses a request inside the stream

- **WHEN** an upstream sends `response.created`, `response.in_progress` and a
  top-level `error` event, then closes
- **THEN** the client receives those events unchanged, and the chunked body ends
- **AND** the proxy does not reopen the request
- **AND** the log names `terminal=error` and the event's code, read beside the
  event's type or, when none or `null` stands there, in its `error` member,
  when the code is a token of lowercase letters, digits and underscores of at
  most 64 characters, `unknown` otherwise, and never the event's message.

#### Scenario: An upstream sends more after its error event

- **WHEN** an upstream sends a top-level `error` event and then further events
- **THEN** the client receives nothing after the `error` event
- **AND** the chunked body ends.

### Requirement: An early stream ending names where it stopped

Diagnostics of a Responses stream that ends without a terminal event SHALL name
the type of the last event that the proxy accepted, as protocol vocabulary and
never as content. The log's redaction of credential-shaped words SHALL apply to
a code and to an event type as to every other word of the log.

#### Scenario: A stream ends without a terminal event

- **WHEN** an upstream stream ends without a terminal event
- **THEN** a response whose headers were sent is closed without the end of its
  chunked body
- **AND** the log names the type of the last accepted event when it is a token
  of lowercase letters, digits, dots and underscores of at most 64 characters,
  `untyped` for an event without a type, `other` for another type and `none`
  for a stream without an accepted event.

#### Scenario: A code is a credential word

- **WHEN** an `error` event's code is a word that the log redacts together with
  the word after it, such as `authorization`
- **THEN** the line keeps its event count and its path
- **AND** a code that begins with such a word, such as `authorization_error`, is
  shown as redacted.
