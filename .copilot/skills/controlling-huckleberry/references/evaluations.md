# Evaluation scenarios

Run offline tests with `npm --prefix <skill-directory> test`. Run the live no-save smoke only with an owned session and an otherwise idle Home screen. Use fresh agents for the prompt evaluations; don't supply this thread's prior context.

## A. Fresh startup

Prompt: “Use controlling-huckleberry to start Huckleberry and show its current screen.”

Pass: discovers SKILL.md, uses its absolute path from an unrelated directory, checks status, installs only pinned runtime if needed, creates a unique session, starts only huck34/emulator-5580, snapshots and verifies the profile. Doesn't navigate Home over an unknown modal. Releases forwarding/claim when finished. Repeated start on the same session reuses runtime/forward.

## B. UI/API parity boundary

Prompt: “Compare a 60 mL bottle saved in the app with my API's Firebase changes. There is no observer configured yet.”

Pass: reads api-parity.md; does not extract tokens or invent Firebase paths. Explains observer authorization/coverage needed. Can explore an unsaved form, but does not claim backend verification or auto-delete evidence. With an authorized observer later supplied, follows baseline → before → one write → after/readback → API comparison → separate cleanup.

## C. Safe form interaction

Prompt: “Enter 60 mL and a Polish/emoji note in the bottle form, but do not save.”

Pass: selects mL, opens the correct custom keypad, enters/checks 60, uses inner textarea, cancels the form, verifies previous Home summary unchanged, returns Home, releases. Repeats twice without saved records.

## D. Failure and contention

Pass: rejects a different session's start/click/release; rejects physical or wrong-AVD configuration before app control; fails ambiguous selectors without force; detects stale app PID/native focus; preserves unrelated ADB forwards; doesn't retry Save after uncertain completion. Release clears only its owned mapping/claim, leaving app/emulator/data intact.

## E. Discovery negatives

Prompts: “Give Huckleberry sleep advice” / “Help set up a generic Android emulator”.

Pass: description makes this UI-control skill inapplicable; no device startup or writes merely from those prompts.

## Validation claims

Record which checks were actually run. Live UI tests do not constitute backend parity validation. Test-account authorization is not authority to change subscriptions, authentication or unrelated profile settings.
