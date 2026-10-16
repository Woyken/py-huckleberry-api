---
name: controlling-huckleberry
description: Starts and controls the Huckleberry Android app on Windows through an existing emulator and WebView UI. Use for Huckleberry app interaction, test-data entry, inspecting saved records, or comparing an app operation with API requests and user-authorized Firebase before/after observations. Not for parenting advice or general Android setup.
compatibility: Native Windows in one logon session, Node 22.12+, existing Android SDK and huck34 AVD, Huckleberry installed with WebView debugging enabled. Initial dependency setup needs npm access; no browser download or Firebase credentials required.
metadata:
  version: "1.0.0"
---

# Controlling Huckleberry

Use the actual app UI as the reference implementation. This skill controls only Huckleberry; it does **not** read Firebase, call its API, capture authentication traffic, or establish backend parity by itself.

## Start from any working directory

Use PowerShell and the loaded skill's base directory. From this repository's root:

```powershell
$skill = (Resolve-Path './.copilot/skills/controlling-huckleberry').Path
$hb = "$skill/scripts/huckleberry.mjs"
node $hb status
# First use / missing dependency only (requires no active skill session):
# node $hb setup
$session = "hb-$([guid]::NewGuid().ToString('N'))"
node $hb start --session $session
node $hb snapshot --session $session
```

If working outside the repository root, set `$skill` to the absolute path of the loaded skill. A user-level installation can instead use `$HOME/.agents/skills/controlling-huckleberry`.

`config.json` defaults to the validated **huck34 / emulator-5580** setup. Before first use on another PC, select an existing AVD and its even emulator console port there; the helper does not install/create an AVD or APK. Keep `serial` emulator-only. Read-only `status` still contacts the configured local emulator if it exists.

`start` boots/reuses the configured emulator, opens Huckleberry, and allocates its own local debugging port. It does not dismiss dialogs or reset the app to Home. Confirm the intended child/profile and authorization to write to that account before entering test data; do not assume every installation is a test account. If logged out, stop and ask the user to sign in manually. If a native dialog is focused, stop and inspect it—do not drive the background WebView.

Give `start` a **300-second shell-tool timeout** for cold boot. A tool timeout does not prove startup or a write failed; inspect status before retrying.

Keep the **same `$session` for the whole investigation**. A different task must wait for release. Claims persist across CLI invocations; commands in a session must also run sequentially, within **one Windows logon session**. Do not share an ID across desktop/RDP/SSH/scheduled-task logons: the command mutex is Windows-session-local. A claim is cooperative and cannot prevent a human or unrelated tool from using the device.

Dependencies live outside the skill under `%LOCALAPPDATA%/huckleberry-agent/runtime`. Run `setup` only before acquiring a session; it installs the lockfile-pinned `playwright-core`, not an emulator, browser or APK. It uses a process-only PowerShell execution-policy override, not a persistent policy change. **Do not install dependencies into this skill directory.** No scripts depend on the original scratch workspace.

## Inspect → act once → verify

```powershell
node $hb inspect --session $session
node $hb click qa:home_bottle --session $session
node $hb wait qa:addFeed_header --session $session
node $hb click qa:addFeed_bottle_units_ml --session $session
node $hb inspect '#feed_bottle_set_amount' --session $session
node $hb click '#feed_bottle_set_amount' --session $session
node $hb keypad 60 --session $session
node $hb fill '[data-qa="addFeed_bottle_notes"] textarea' 'AGENT UI TEST unique-marker' --session $session
# Form-only practice: cancel, do not save.
node $hb click qa:addFeed_close --session $session
```

- `qa:name` selects an existing `data-qa` attribute; otherwise selectors are CSS. Prefer Ionic **host** QA selectors, not role-selected shadow buttons. Ambiguous selectors fail; never choose `.first()` or force a click.
- `keypad` updates the open bottle **mL** form; it does **not** save the record. Reinspect before a record-save click.
- `snapshot` includes accessibility **and** visible DOM controls; an empty/partial accessibility tree is an observed app quirk. Do not remove `aria-hidden`, change the app's framework state, or mutate storage to make automation work.
- Use `read` and `wait --text` to verify postconditions. A click success or stable snapshot is not evidence of a persisted write. Reopen the exact record in Reports > List > Details > Edit.
- Saved quantities may display in different account units. Check the reopened value/unit and event time, not just the Home card. Locked premium reports contain demo values.

Read [controls and recipes](references/controls.md) for selectors, time pickers, save/read/delete, reusable Node API, smoke testing and error recovery. Run `node $hb help` for the full CLI.

## API/Firebase parity investigations

**Before saving anything, read [the parity protocol](references/api-parity.md).** It includes the observer boundary, no-save baseline, timestamps/units, uncertain-write recovery, and a report template.

**For a parity investigation, without an authorized observer and captured backend before-state, do not Save or perform another backend-changing action. Stop and offer unsaved exploration only.** This does not prohibit explicitly requested standalone UI tests; label those UI-only and do not claim parity.

1. Identify the one operation and the project's **authorized backend observer**. Do not invent document paths, read app tokens or silently substitute a reverse-engineered API for the app operation.
2. Capture server before-state and a UI checkpoint. Perform exactly one intended app action. Pause for the observer to record after-state, including related summaries/timers, before any cleanup or API experiment.
3. Verify the exact marked entry through the app. Compare backend changes with the API operation under equivalent starting conditions; label missing evidence **unverified**, not equivalent.
4. Save evidence first. Delete only explicitly requested/created fixture records and capture deletion separately. **Never auto-clean up a fixture before its Firebase evidence is preserved.**

```powershell
node $hb checkpoint --out './ui-before.json' --session $session
# Coordinate the project observer and one intended UI action.
node $hb checkpoint --out './ui-after.json' --session $session
```

Checkpoints contain UI/device time evidence only (`backendCaptured: false`) and refuse to overwrite files. Store evidence in the investigation's directory, not this skill. Screenshots, notes and child data remain private even in a test account.

## Finish or recover

```powershell
node $hb release --session $session
```

Release removes only the exact forwarding rule allocated to this claim. It leaves the emulator, app and records intact. Errors retain the claim for reconciliation; use `status`, then retry `start` with the **same** session if the app restarted. Never retry an uncertain Save/delete until the observer and UI establish whether it already happened. Do not steal another session, restart the shared ADB server, change profile settings, purchase upgrades, modify the APK, disable virtualization/security, or wipe an AVD.

After an agent crash, the Windows command mutex is released automatically; the same task can retry with its original session. A fresh conversation may resume/release a previous owner ID **only after the user confirms that task ended** (or its owning delegated task is explicitly terminal). Record that recovery and reconcile in-flight writes first. A live command still cannot be overridden. See controls.md for malformed legacy claims.

If CDP is unavailable in an app build, stop and report that limitation; do not silently change automation methods mid-experiment. [Evaluation scenarios](references/evaluations.md) define acceptance tests for maintaining this skill.
