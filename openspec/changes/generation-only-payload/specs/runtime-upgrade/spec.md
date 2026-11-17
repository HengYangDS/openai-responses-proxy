## ADDED Requirements

### Requirement: A payload is read only through a selected generation

The lifecycle SHALL read an installed payload only through the generation that the selector under the stable
control root names. Payload files at the control root's own level, without a selector and without a transaction,
SHALL be unverified content, whatever manifest accompanies them.

#### Scenario: Payload files lie at the control root without a selector

- **WHEN** the control root holds an executable, a payload manifest, a runtime carrier and an installed record at
  its own level, with no selector and no transaction
- **THEN** `status` reports neither `running` nor `stopped` and names no verified payload
- **AND** `uninstall --purge` exits nonzero with an ownership diagnostic and preserves every byte
- **AND** `rollback` and `reload` change no file, service or process.

#### Scenario: A serving process was loaded from outside the generation store

- **WHEN** a serving process whose payload does not lie in a generation of a control root resolves the selected
  payload for a handoff
- **THEN** the resolution fails before a successor is started.

### Requirement: A successful upgrade retains exactly one predecessor generation

A successful upgrade SHALL retain exactly one verified predecessor generation
after the successor has proved accepting runtime identity.

#### Scenario: Complete requirement obligations are evaluated

- **WHEN** this requirement is evaluated for implementation or acceptance
- **THEN** One atomic selector
  under the stable control root SHALL be the sole authority naming the active
  immutable generation and its optional predecessor. Installed state and command
  projection SHALL remain stable control surfaces outside both generations. A
  transaction snapshot MAY exist only as temporary bootstrap or recovery
  evidence and SHALL NOT become another retained product store. A subsequent
  successful upgrade SHALL replace the older predecessor in the selector. Fresh
  installation SHALL select no predecessor.

#### Scenario: Upgrade finalizes successfully

- **WHEN** the successor payload, command, native supervisor, and listener have
  proved the committed successor identity
- **THEN** finalization atomically selects the successor as active and the
  displaced active generation as its sole predecessor
- **AND** removes the active transaction without copying either selected
  generation into a parallel rollback store.

#### Scenario: Finalization is interrupted around selection

- **WHEN** finalization stops after the predecessor generation is materialized
  or after its selector is committed
- **THEN** the active transaction remains the sole recovery authority
- **AND** retry advances the same generation to the terminal selected state
- **AND** the previously selected generation is not removed before the new
  selector is durable.

#### Scenario: A second upgrade succeeds

- **WHEN** an installation with a retained predecessor upgrades again
- **THEN** the newly displaced release becomes the sole retained predecessor
- **AND** no older rollback history remains.

## REMOVED Requirements

### Requirement: Successful upgrade retains one exact predecessor

**Reason**: Its scenario "The retained-generation transition is introduced" describes an upgrade from an installation without generations, which the product has not performed since 4.0.0, and OpenSpec has no operation that removes one scenario.

**Migration**: "A successful upgrade retains exactly one predecessor generation" keeps the statement and the other four scenarios word for word.
