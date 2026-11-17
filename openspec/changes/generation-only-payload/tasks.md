## 1. Fixtures

- [x] 1.1 Give every test context a payload in a generation: the fixtures `install_context`, `platform_context`
      and `temporary_context` name an executable under the generation store, and `install_payload` writes there
      without changing the context. A test that asserts a literal executable path asserts the generation's. The
      release fixture `runtime_context_for` describes an installation that a built executable makes, whose
      generation it cannot name: it keeps its path until 3.1 gives it the control root's context. Done when the
      suites pass with the counts of the base and the fixtures name no path at the control root's own level.

## 2. No payload without a selection

- [x] 2.1 Remove the accepted root-level locations: the runtime carrier in `runtime_spec`, the control root of a
      serving process outside the generation store in `serving/identity`, the launch agent's executable on
      macOS. A test shows each refused. The root context of the release verification tool goes with 3.1.
- [x] 2.2 Bind the installed record to the selection where it is compared (`installed_names`) and in
      `rollback`; a test shows a record refused without a selection even with a payload identity in hand,
      and a root-level payload with a valid manifest and an installed record reported neither `running` nor
      `stopped`.
- [x] 2.3 Purge the generations of the store alone. A test shows `uninstall --purge` of a root-level payload
      with a valid manifest failing with every byte preserved and no journal written, a selected generation
      without an installed record still purged by its own manifest, and the purge of an interrupted purge's
      remaining generations completing without a selector.

## 3. Two contexts

- [x] 3.1 Divide the context into the control root's and a payload generation's; `selected_context` and
      `control_context` return a payload context or nothing; the platform is a field set from the host. The
      strict type check passes with `executable` on the payload context alone.
- [x] 3.2 `context.create` reads the settings of the selected generation's carrier, or the defaults; `status`
      and `reload` without a selection read no payload location and report "no payload generation is
      selected". Tests cover an absent control root, an empty one and one with unverified content.
- [x] 3.3 `uninstall` without a payload deregisters the control root's service, ends the processes of the
      generations that the store holds, and removes no command that it cannot prove. Tests cover an empty
      control root and an interrupted purge.
- [x] 3.4 A first installation's command record names its candidate's executable, and the restoration without a
      previous selection requires the command to be absent. Tests cover a first installation that fails before
      and after its activation, its recovery, and a record of 6.0.2 built by hand.

## 4. Specification and release

- [x] 4.1 The delta of `runtime-upgrade` validates strictly, and a simulated archive leaves the main
      specification valid.
- [x] 4.2 `VERSION` and `CHANGELOG.md` name 6.0.3 with the removal under its own heading.

## 5. Verification

- [ ] 5.1 `mise run check` passes on the lane head.
- [ ] 5.2 The hosted graph passes at the proposal head on both forges, with the journeys from the published
      6.0.2 on macOS, Linux and Windows.
- [ ] 5.3 A fresh-context review of the whole change finds no order or state in which a command reads a
      payload outside a generation, or its findings are answered.
