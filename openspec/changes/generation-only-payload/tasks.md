## 1. Fixtures

- [ ] 1.1 Give every test context a payload in a generation: the fixtures `install_context`, `platform_context`,
      `temporary_context` and `runtime_context_for` name an executable under the generation store, and
      `install_payload` writes there without changing the context. The tests that assert a root-level executable
      or carrier path assert the generation's. Done when the suites pass and no test outside those of 2.1 to
      2.3 names a payload at the control root's own level.

## 2. No payload without a selection

- [ ] 2.1 Remove the accepted root-level locations: the runtime carrier in `runtime_spec`, the control root of a
      serving process outside the generation store in `serving/identity`, the launch agent's executable on
      macOS, and the root context of the release verification tool. A test shows each refused.
- [ ] 2.2 Bind the installed record to the selection in `status` and `rollback` whenever a record exists; a test
      shows a root-level payload with a valid manifest and an installed record reported neither `running` nor
      `stopped`.
- [ ] 2.3 Purge the generations of the store alone. A test shows `uninstall --purge` of a root-level payload
      with a valid manifest failing with every byte preserved, and the purge of an interrupted purge's
      remaining generations completing without a selector.

## 3. Two contexts

- [ ] 3.1 Divide the context into the control root's and a payload generation's; `selected_context` and
      `control_context` return a payload context or nothing; the platform is a field set from the host. The
      strict type check passes with `executable` on the payload context alone.
- [ ] 3.2 `context.create` reads the settings of the selected generation's carrier, or the defaults; `status`
      and `reload` without a selection read no payload location and report "no payload generation is
      selected". Tests cover an absent control root, an empty one and one with unverified content.
- [ ] 3.3 `uninstall` without a payload deregisters the control root's service, ends the processes of the
      generations that the store holds, and removes no command that it cannot prove. Tests cover an empty
      control root and an interrupted purge.
- [ ] 3.4 A first installation's command record names its candidate's executable, and the restoration without a
      previous selection requires the command to be absent. Tests cover a first installation that fails before
      and after its activation, its recovery, and a record of 6.0.2 built by hand.

## 4. Specification and release

- [ ] 4.1 The delta of `runtime-upgrade` validates strictly, and a simulated archive leaves the main
      specification valid.
- [ ] 4.2 `VERSION` and `CHANGELOG.md` name 6.0.3 with the removal under its own heading.

## 5. Verification

- [ ] 5.1 `mise run check` passes on the lane head.
- [ ] 5.2 The hosted graph passes at the proposal head on both forges, with the journeys from the published
      6.0.2 on macOS, Linux and Windows.
- [ ] 5.3 A fresh-context review of the whole change finds no order or state in which a command reads a
      payload outside a generation, or its findings are answered.
