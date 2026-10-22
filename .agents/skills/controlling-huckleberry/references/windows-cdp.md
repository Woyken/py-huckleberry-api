# Windows CDP setup and recovery

This is the `windows-cdp` route only. Choose it explicitly before starting an investigation. It is not available in Termux and is not interchangeable with the shared accessibility route. The CDP host claim does not coordinate with accessibility device claims; do not operate the same app through both.

## Startup from any directory

Use PowerShell and the loaded skill's base directory. From the repository root:

```powershell
$skill = (Resolve-Path './.agents/skills/controlling-huckleberry').Path
$hb = "$skill/scripts/huckleberry.mjs"
node $hb status
# First use / missing dependency only; no active CDP session:
# node $hb setup
$session = "hb-$([guid]::NewGuid().ToString('N'))"
node $hb start --session $session
node $hb snapshot --session $session
```

Outside the repository set `$skill` to the loaded skill's absolute directory. A bundled user-level CDP installation can use `$HOME/.agents/skills/controlling-huckleberry`.

`config.json` defaults to the validated **huck34 / emulator-5580** setup. On another PC choose an existing AVD and its even emulator console port before first use. The helper does not install/create an AVD or APK. `status` reads the configured local emulator if present; it does not boot it.

`start` boots/reuses that emulator, opens Huckleberry, and allocates a local debugging port. It does not dismiss dialogs or navigate Home over existing work. Confirm the intended child/profile and write authorization from the UI; do not assume this is a test account. If logged out, ask the user to sign in manually. Native focus mismatch means inspect the actual Android window, not the background WebView.

Give cold `start` a **300-second host-tool timeout**. An external timeout is not proof that startup or an action failed. Inspect status and reconcile before retrying.

## Dependency and task ownership

Dependencies live outside skill discovery under `%LOCALAPPDATA%/huckleberry-agent/runtime`. `setup` installs the lockfile-pinned Playwright package, not a browser/emulator/APK, and needs npm access. It uses a process-only PowerShell policy override, not a persistent security change. Never install dependencies inside the skill folder.

Keep the same session for the whole task. Commands run sequentially within **one Windows logon session**; do not share an ID across desktop/RDP/SSH/scheduled-task logons. Task claims and the local OS mutex are cooperative and cannot prevent a human or unrelated tool from using the app. They are not shared with the accessibility bridge.

## Form-only example (no record save)

```powershell
node $hb click qa:home_bottle --session $session
node $hb wait qa:addFeed_header --session $session
node $hb click qa:addFeed_bottle_units_ml --session $session
node $hb inspect '#feed_bottle_set_amount' --session $session
node $hb click '#feed_bottle_set_amount' --session $session
node $hb keypad 60 --session $session
node $hb fill '[data-qa="addFeed_bottle_notes"] textarea' 'UNSAVED AGENT INPUT TEST' --session $session
node $hb click qa:addFeed_close --session $session
```

The custom keypad updates the open form only; its Save is not the tracking-record Save. For an intended record write, follow the skill's controls/parity references, use a unique fixture note, check units/event time, and reopen the exact Reports entry. Never retry an uncertain Save based on a stale Home summary.

## Private UI evidence

```powershell
node $hb checkpoint --out './ui-before.json' --session $session
# One authorized supported action, coordinated with the backend observer.
node $hb checkpoint --out './ui-after.json' --session $session
```

Checkpoints contain UI/device time evidence only (`backendCaptured: false`), not Firebase snapshots. Files refuse overwrite. Do not confuse their schema with accessibility protocol-versioned trees or upload raw snapshots/screenshots/account data publicly.

## Release and interrupted operations

```powershell
node $hb release --session $session
```

Release removes only this claim's exact forwarding rule, leaving emulator/app/records intact. Errors retain the task claim. If the app process restarted, run `start` with the same session to reconnect, then reconcile in-flight writes before new input. If `start` is interrupted after ADB allocates a forward but before the claim records it, the persisted pending record makes the next `start` or `release` remove only the newly allocated matching rule; forwards present before allocation are preserved.

The command mutex is released on a command-process crash. A fresh conversation may resume/release a previous task ID only after user confirmation that its owner ended (or a verified terminal owning delegated task). A live command cannot be overridden. Do not delete broad state directories or restart the shared ADB server.

Unavailable CDP, unknown focus or a broken accessibility snapshot is not permission to switch drivers mid-investigation, remove `aria-hidden`, patch the app or mutate framework/storage state. Stop and report unsupported conditions.
