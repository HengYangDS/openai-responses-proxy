## 1. Declaration and fill

- [ ] 1.1 Add the module that reads the declared hours and fills Renovate's, uv's and npm's settings. Done when its
      tests pass: the fill follows a declaration of 0, 1, 24, 25 and 72 hours and leaves every other line, a
      declaration that is not whole hours is refused, the check names a file that differs, and a refusal writes
      nothing.
- [ ] 1.2 Declare 24 hours in the Mise configuration, fill the three settings and refresh `uv.lock`. Done when
      `uv lock --check` passes under the filled setting and the lock's packages are those of `dev`.
- [ ] 1.3 Run the fill and the lock refresh in the `fix` session and the check in the governance graph. Done when
      the governance composition test lists the check once and `mise run fix` restores an edited npm line.
- [ ] 1.4 Replace the dependency policy test's refusal of an age in uv's and npm's configuration with the
      declared one. Done when the dependency policy tests pass.
- [ ] 1.5 Hold each setting by the value that its resolver reads, and refuse another age rule in uv's or npm's
      configuration. Done when the module's tests pass: a filled text is read again before any write and refused
      unless it holds the declared age, a setting written twice or where its resolver does not read it is
      refused, a missing uv line goes above the first table, and the check names `before`,
      `min-release-age-exclude` and any uv age key other than `exclude-newer` and `exclude-newer-package`.

## 2. Documents

- [ ] 2.1 Describe the declaration, the fill and a security fix under the age in the governance document, and
      name the filled files under the dependency proposal control of the hard-coding policy. Done when the
      documentation and hard-coding tests pass. The changelog keeps user-visible changes of the product, so it
      gets no entry.

## 3. Verification

- [ ] 3.1 Pass `mise run check` on the lane head.
- [ ] 3.2 Pass the proposal's hosted checks on both forges, the Windows jobs included.
