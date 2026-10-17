# Controls and recipes

**Windows CDP route only.** These selectors, form/typing/screenshot commands and checkpoint format do not apply to the shared accessibility CLI. Choose the route in SKILL.md before starting; do not switch mid-investigation.

## Contents
- Scope and selectors
- Bottle form and readback
- Sleep, dates and other forms
- Checkpoints and screenshots
- Reusable Node API and smoke test
- Recovery and installation
- Verified environment

## Scope and selectors

Use the `node $hb ... --session $session` prefix established in the Windows CDP setup reference linked directly from SKILL.md. Commands return JSON. Observations are read-only; `click`/`fill` can change app state. Commands never automatically retry a UI action.

`inspect` lists visible controls, scoped to the top open Ionic overlay when present. `snapshot` always includes QA controls alongside accessibility to expose omissions caused by stale `aria-hidden`. Inspect output may still contain clipped/offscreen controls. Playwright scrolls before clicking and rejects intercepted clicks; do not force them.

`click`, `fill`, `read`, `wait` require an explicit CSS or `qa:` selector. Underlying Playwright strictness rejects duplicates. Behind an overlay, only act on overlay controls, not the underlying page. For ambiguous controls, inspect and narrow the selector; no arbitrary first-match fallback.

| Purpose | Selector |
| --- | --- |
| Home / Reports / Child | `#tab-button-home` / `#tab-button-stat-tabs` / `#tab-button-child-info` |
| Selected Reports tab | `#tab-button-stat-tabs.tab-selected` |
| Reports List / Summary | `qa:statTabs_header_tab_list` / `qa:statTabs_header_tab_summary` |
| Open sleep / bottle / diaper | `#home_sleep` / `qa:home_bottle` / `qa:home_diaper` |
| Last bottle summary | `qa:home_lastBottle` |

Ionic's native shadow button can be visually covered by its host. Prefer the host's QA selector over `getByRole('tab')` on segment controls. Some `hb-item` wrappers do not handle a center click; click their `.value` child (or a specific span ID) instead.

## Bottle form and readback

| Purpose | Selector |
| --- | --- |
| Form ready / close | `qa:addFeed_header` / `qa:addFeed_close` |
| Select mL | `qa:addFeed_bottle_units_ml` |
| Open amount keypad | `#feed_bottle_set_amount` |
| Keypad / title | `qa:numpad` / `qa:numpad_title` |
| Commit keypad / cancel keypad | `qa:numpad_button2` / `qa:numpad_button1` |
| Notes | `[data-qa="addFeed_bottle_notes"] textarea` |
| **Save tracking record** | `#addFeed_footer_saveButton` |
| Start time / milk type | `#addFeed_bottle_setStartTime` / `#addFeed_bottle_setBottleType` |

`keypad 60` requires an already-open **Volume (ml)** dialog. It clears/sets/checks the displayed integer, commits it to the form and checks the form amount. It never presses the record Save button. Decimals, ounces and date pickers require inspection and task-specific control; do not reuse this helper for them.

Set a unique note such as `AGENT UI TEST <ISO timestamp> <case>` before saving test data. Use an explicit event time when comparing with an API; opening a form defaults its time, and it may remain unchanged while the agent works.

After one intended Save:
1. Wait for `qa:addFeed_header --hidden`; don't assume this proves sync.
2. Open Reports, then List using the host QA selector. Wait for `qa:listView`.
3. Locate the list item containing the **exact unique note**, not an ordinal from another run.
4. Click it to open Details. Check `qa:sessionDetail_notes`, `qa:sessionDetail_title`, `qa:sessionDetail_date`, `qa:sessionDetail_time`.
5. Verify the exact note in Details using `read qa:sessionDetail_notes`. `qa:sessionDetail_edit` opens the editor: read `#feed_bottle_set_amount`, units and event time; use `qa:addFeed_close` to leave without another Save. The CLI deliberately does not read textarea/password values. For notes, Details is the reliable readback even if accessibility is incomplete.

List QA names such as `listView_slidingItem_0_0` are dynamic. Use observed values only, or a reusable Node locator filtered by the exact marker.

**Delete only after evidence collection:** confirm `qa:sessionDetail_notes` is the intended fixture, click `qa:sessionDetail_header_delete`, inspect the confirmation, then confirm its scoped Yes button. Verify the marked row disappears and capture the observer's deletion separately. `qa:sessionDetail_done` closes Details without deletion.

## Sleep, dates and other forms

- Sleep: `qa:sleep_header`, `qa:sleep_close`, `qa:sleep_startTimePicker`, `qa:sleep_endTimePicker`, `qa:hb-timer-button`. Clicking START changes timer/backend state; coordinate an observer first.
- A sleep time picker opens by clicking the inner `.value` / “Set time”, not necessarily the whole wrapper. Common controls: `qa:timePicker`, `qa:timePicker_cancel`, `qa:timePicker_save`.
- Date/time wheels contain many clipped/repeated options. Inspect DOM and screenshot; do not assume the first text match is selected. Don't inject dates into Angular/Ionic component internals.
- Diaper: `qa:diaper_close`, `qa:diaperPotty_modeSelector_pee` / `_poo` / `_both` / `_dry`. Choosing a mode can change the form; inspect before Save. Only close/open paths have been tested for this form.
- Child profile contains CSV export. Do not email an export or change profile/day-night settings without the task requiring it.

## Checkpoints and screenshots

```powershell
node $hb checkpoint --out './case-ui-before.json' --session $session
node $hb screenshot --out './case-ui-before.png' --session $session
node $hb read '#feed_bottle_set_amount' --session $session
node $hb wait '#feed_bottle_set_amount' --text '60 ml' --exact --session $session
```

Outputs refuse overwrite. Paths are relative to the caller's working directory. Checkpoints include host UTC observation time, device time/offset and device timezone, and explicitly mark backend data absent. They don't prove screenshots/snapshots are atomic with backend reads. UI snapshots include private data; don't commit or upload them automatically.

`wait --text` is a substring match; use `--exact` for quantities so “60 ml” cannot match “160 ml”. `wait --hidden` checks that all matches are hidden, including cached duplicate pages.

## Reusable Node API and smoke test

When a task needs scoped markers or a longer sequence, import the provided module instead of rewriting the CDP connection. This example runs from the repository root:

```javascript
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const { withHuckleberry, locate } = await import(pathToFileURL(
  resolve('.agents/skills/controlling-huckleberry/scripts/ui.mjs')
).href);
await withHuckleberry('the-same-task-session', async (page) => {
  const row = page.locator('[data-qa^="listView_slidingItem_"]').filter({ visible: true, hasText: 'the-exact-unique-note' });
  if (await row.count() !== 1) throw new Error('Expected exactly one fixture');
  await row.click();
  await locate(page, 'qa:sessionDetail_notes').waitFor();
});
```

Use the actual loaded skill's absolute path when working outside the repository root or using a user-level installation. `withHuckleberry` holds the session's command lock for the entire callback and disconnects afterwards. Do not run CLI commands concurrently inside it (they will correctly refuse the lock). Do not inspect storage, authentication traffic or framework internals in the callback.

`node "$skill/scripts/smoke.mjs" --session $session` tests the current Home screen, bottle keypad, Unicode notes, cancellation, Reports List and return Home. It **never saves a record**. Run only outside a parity capture window, with no modal open or unsaved user work. It compares before/after Home text rather than hardcoding account values.

## Recovery and installation

- Missing runtime: release your session, run `node $hb setup`, then start a fresh session. The helper uses a pinned lockfile and places dependencies outside all skill-discovery directories.
- App PID changed or forwarding missing: `start` with the same session. It replaces only its exact stale mapping and never touches another tool's mapping.
- Wrong AVD at the console port: stop; change `config.json` only when no claim is active, and only to an explicitly selected emulator/AVD. Physical devices are rejected.
- Native dialog/focus mismatch: no WebView interaction. An explicit `adb -s emulator-5580 shell screencap` can help inspect the actual Android screen. Don't restart ADB or dismiss unknown prompts blindly.
- Different session owner: wait for that task. After a crash, ask the user to confirm the old task ended (or verify the owning delegated task is terminal), record the approved recovery, then resume/release with the exact previous owner ID. Reconcile any uncertain write first. This is not permission to take an active claim from its owner.
- Command lock: a Windows named mutex is held by a helper that exits on Node's stdin EOF. Command crashes therefore do not require stale-file deletion or PID-based ownership decisions; a live command is never overridden. `command.json` is diagnostic only. Same-session retry works without releasing the task claim. An unexpectedly lost mutex keeper terminates the command and makes in-flight writes uncertain.
- Run every command of a task within one Windows logon session; the command mutex uses the `Local` namespace. Do not reuse the same task ID from a second desktop/RDP/SSH/scheduled-task logon. Task claims still prevent a different task from taking over.
- Start failure retains its claim. Inspect `status`, finish/reconcile any uncertain write, then release your session. Incomplete/malformed claims require inspecting the exact directory reported in the error; do not recursively delete broad state folders.
- CDP socket unavailable: stop and report; do not patch APKs or silently substitute backend writes.

## Verified environment

Validated against existing `huck34` (Android 14 Google APIs x86_64), package `com.huckleberry_labs.app`, `.MainActivity`, app version `0.9.305`, and `playwright-core@1.64.1`. These are observations, not guarantees for later app builds. Test fixtures/child names are not hardcoded into the scripts. The runtime consistently selects the SDK's ADB binary when available and reports it in `status`; it never runs `kill-server`.

Before client commands, the helper compares selected/PATH client wire protocols and queries the existing shared server's wire protocol directly over localhost without invoking a restarting client. A mismatch fails closed. Different package/build versions alone are not a wire mismatch (both installed builds here use protocol 41); status reports the difference. No ADB server is deliberately restarted.
