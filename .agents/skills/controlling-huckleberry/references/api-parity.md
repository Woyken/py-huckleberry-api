# API/Firebase parity protocol

## Boundary

The app is the UI reference. This skill does not know whether a particular feature writes Firestore, Realtime Database, native storage or another service. It supplies **UI control and evidence**, not a Firebase observer or schema. Never claim backend equivalence from UI similarity alone.

Choose one explicitly supported backend before the experiment. The shared accessibility route supports inspection, Home/Reports navigation and semantic node actions, including form controls; Windows CDP's screenshots/DOM commands must not be inferred for Termux. Accessibility has no backend observer and no screenshots, so its UI state alone cannot verify persistence. Keep backend/protocol/source and evidence coverage in the report. CDP checkpoints and accessibility trees have different schemas and independent claims; no mixed-driver workflow or unified cross-driver lock is supported.

Use the investigation project's already-authorized observer/client or a user-provided server-side read mechanism. Keep secrets in that project's approved secret handling; do not add them to this skill, command arguments, notes, screenshots or reports. Do not extract app tokens from preferences, WebView storage, CDP Network/headers, or native files. Missing observer authorization is a blocker for **backend verification**, not for read-only app exploration.

**In a parity investigation, do not Save, start a timer, delete, or perform another backend-changing action until an authorized observer is available and a server before-state has been captured.** This applies to accessibility `click`/`set-text` actions as well as CDP. If either is missing, stop and offer unsaved exploration only. If the observer fails after an already-authorized action, preserve the fixture and report the after-state as unverified—never blindly repeat the write. Explicit standalone UI-only tests are a different task and cannot establish parity.

## Investigation checklist

- [ ] Identify one operation, child/profile, account, initial state, input values and intended time/unit semantics. User permits writes on this account; reconfirm if a different profile/account is selected.
- [ ] Identify the authorized observer and which backend documents/subtrees it actually covers. Require server-confirmed reads, not a stale SDK offline cache. Record any coverage gaps.
- [ ] Quiet the experiment: no other agent, phone/client or active timer is changing the same data. Do not stop existing timers merely to manufacture a baseline; account for them or ask.
- [ ] Record host/device clocks, device timezone/offset and UI display units. Use host UTC checkpoints plus backend read timestamps; note observable skew and asynchronous ordering. Do not change clocks/timezones for convenience.
- [ ] Measure baseline noise: observe backend before/after opening and cancelling an unsaved form. If changes occur, investigate rather than attributing every later change to Save.
- [ ] Save a server **before** snapshot and a UI **before** checkpoint into the investigation directory. Define a unique `AGENT UI TEST <ISO timestamp> <case>` marker if the form supports notes. For accessibility, preserve the inspected tree and action ID; for CDP, preserve the checkpoint.
- [ ] Fill the form and verify values, units, timestamps and note. Capture prepared UI state if useful. Separate all preparation effects from the one write being measured.
- [ ] Perform exactly **one intended app write** (Save, timer start, edit or delete). Do not click twice because the screen looks slow. Record the action's time window.
- [ ] Poll the observer within a declared bound until the intended marker/change is server-visible, preserving read times and errors. If unavailable/timeout, mark backend evidence **unverified**; retain the fixture and report it.
- [ ] Save backend **after** and UI **after** evidence before cleanup. Read back the exact record from Reports > List > Details > Edit, not just a stale Home summary.
- [ ] Diff the whole relevant observed subtree, including related summary/timer documents and implicit fields. Explicitly distinguish observed absence from observer coverage gaps.
- [ ] Compare the API operation under equivalent starting conditions using a **different marker**. Don't replay on the already-mutated UI fixture unless testing updates intentionally. Keep app and API actors separate and changes serialized.
- [ ] Map semantic equivalence: stored numeric precision/unit, missing vs null/default, timestamp type/epoch/offset, event time vs creation time, IDs, summary updates, timer state and ordering. Mark unmapped fields **unknown**. Do not silently ignore differing fields to manufacture a match.
- [ ] Only after evidence is preserved, perform requested cleanup of exact fixture IDs/markers. Capture deletion as a **separate operation**, not part of the original Save diff.
- [ ] Release the skill session and report remaining fixtures or uncertainty.

## Uncertain write recovery

If a click/action times out, loses CDP, or the app restarts, the write may already have happened. Reconnect with the same session and action ID, check `action-status` on accessibility, and consult the observer/Reports for the exact marker before retrying. A duplicate is not harmless merely because this is a test account. Don't automatically save or delete in a `finally` block.

If the feature has no notes, correlate with the exact child, type, event time, value and observer-produced ID. Avoid unrelated writes in the capture window. Insufficient correlation means **unknown**, not permission to delete the closest-looking pre-existing record.

## Suggested evidence layout

Keep these in the **project's investigation directory**, never under the installed skill:

```text
case-name/
  protocol.md             # observer provenance/coverage, initial state, case marker, clocks/units
  ui-before.json
  backend-before.json
  ui-prepared.json        # optional, captures form just before Save
  backend-after.json
  ui-after.json
  backend-diff.json       # generated by the project's comparison tooling
  api-before.json
  api-after.json
  cleanup-after.json      # only if cleanup requested and verified
  result.md
```

No observer command is invented here: use the project's actual interface and schema. There is deliberately no bundled “diff” that normalizes away timestamps/IDs without understanding their meaning.

## Report template

```markdown
## Operation
- App build / profile / fixture marker:
- App input, event time and display units:
- API operation and equivalent starting state:

## Evidence
- Authorized observer and server-read coverage:
- Before → action window → after timestamps, device timezone/clock skew:
- Persisted record and related-document changes:
- UI readback (including any unit conversion):

## Comparison
- Matching semantics:
- Differences:
- Unknowns / noise / missing coverage:
- Verdict: equivalent within stated coverage | different | unverified

## Cleanup
- Fixture IDs/markers removed or intentionally retained:
- Separate deletion observation:
- Skill session released:
```

Example pitfall: a bottle entered as 123 mL may show as 4.16 oz in Reports. That display conversion alone neither proves a storage bug nor establishes the backend's canonical unit. Reopen Edit and inspect the authorized server evidence.
