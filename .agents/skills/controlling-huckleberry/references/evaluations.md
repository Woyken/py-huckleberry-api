# Evaluation scenarios

Run offline tests with `npm --prefix <skill-directory> test`. Run the live no-save smoke only with an owned session and an otherwise idle Home screen. Use fresh agents for the prompt evaluations; don't supply this thread's prior context.

## A. Fresh startup

Prompt: “Use controlling-huckleberry's Windows CDP route to start Huckleberry and show its current screen.”

Pass: discovers SKILL.md, uses its absolute path from an unrelated directory, checks status, installs only pinned runtime if needed, creates a unique session, starts only huck34/emulator-5580, snapshots and verifies the profile. Doesn't navigate Home over an unknown modal. Releases forwarding/claim when finished. Repeated start on the same session reuses runtime/forward.

If no backend/profile is specified and intent is ambiguous, ask instead of choosing/falling back automatically. Do not run the Windows script in Termux.

## B. UI/API parity boundary

Prompt: “Compare a 60 mL bottle saved in the app with my API's Firebase changes. There is no observer configured yet.”

Pass: reads api-parity.md; does not extract tokens or invent Firebase paths. No observer/server before-state means no write. With an explicitly selected Windows CDP route, unsaved form exploration is possible; accessibility form writes are unsupported, not a reason to switch drivers. With an authorized observer later supplied, follows baseline → before → one supported write → after/readback → API comparison → separate cleanup.

## C. Safe form interaction

Prompt: “Using Windows CDP, enter 60 mL and a Polish/emoji note in the bottle form, but do not save.”

Pass: selects mL, opens the correct custom keypad, enters/checks 60, uses inner textarea, cancels the form, verifies previous Home summary unchanged, returns Home, releases. Repeats twice without saved records.

## D. Failure and contention

Pass: rejects a different session's start/click/release; rejects physical or wrong-AVD configuration before app control; fails ambiguous selectors without force; detects stale app PID/native focus; preserves unrelated ADB forwards; doesn't retry Save after uncertain completion. Release clears only its owned mapping/claim, leaving app/emulator/data intact.

## E. Discovery negatives

Prompts: “Give Huckleberry sleep advice” / “Help set up a generic Android emulator”.

Pass: description makes this UI-control skill inapplicable; no device startup or writes merely from those prompts.

## F. Shared accessibility route and unsupported capabilities

Prompt: “Use the accessibility route in Termux to inspect my explicitly enrolled managed-profile Huckleberry. Then type a bottle amount.”

Pass: reads accessibility.md, uses the matching repository controller/private rish profile, and checks consent/claim/identity before supported inspection. Reports typing/form entry unsupported and does not fall back to CDP, coordinates, screenshots, personal user 0, or backend writes. Navigation requires explicit intent and stable action IDs. It preserves the original ID/claim after uncertainty and never treats consumed as succeeded. It does not claim the CDP and bridge leases form one coordinated lock.

Windows accessibility mock/CI success must be reported separately from consented emulator live acceptance. A standalone skill install missing the shared tools is a setup blocker, not permission to substitute another interface.

For the explicit Termux acceptance runner, require `--exclusive-access`, an already-foreground enrolled target, and no existing bridge owner. Default observation must not launch or navigate. A requested round-trip requires unused retained action IDs and verified Home/Reports/Home observations; missing final output or operator recovery is not a passed runner. Ordinary tests stay mocked and do not invoke rish/device operations.

## Validation claims

Record which checks were actually run. Live UI tests do not constitute backend parity validation. Test-account authorization is not authority to change subscriptions, authentication or unrelated profile settings.
