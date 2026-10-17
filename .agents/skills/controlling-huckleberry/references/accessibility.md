# Shared accessibility workflow

This is the `accessibility` route: the existing `tools/huckleberry-control/` controller and consented Android bridge. It works through explicitly enrolled Windows ADB emulators or Termux/Shizuku rish managed profiles. It is developer tooling, not part of the Python API product. Protocol 1, controller/transport code and bridge capabilities are unchanged by skill routing.

## Setup boundary (read before device access)

Read `tools/huckleberry-control/README.md` in the matching repository checkout for the complete build/install/manual-consent and enrollment procedure. The skill does not bundle the helper APK, a signing key, a profile, or a backend observer. A standalone user-profile skill copy is insufficient for this route.

Use an existing private profile derived from the repository's example for the explicitly authorized target. Placeholders intentionally fail. Never invent an Android user, expected child, serial or AVD, and never commit the real profile.

- ADB targets an exact existing **emulator**; never a physical phone.
- Termux/rish targets an enrolled **managed profile**; never personal Android user 0. The helper itself may be installed in the parent user as documented; that does not authorize controlling its personal Huckleberry copy.
- The helper is a separate app with broad accessibility visibility inherently. Its manual enablement/installation/update requires explicit consent. Do not write secure settings, remove managed-profile restrictions, or patch Huckleberry as recovery.
- This controller does not boot AVDs, install apps, enable services or select a child. It has no generic selectors, form input, Save/delete/timer, screenshot or coordinate commands.
- Both backends have separate claims, not unified locking. Use only one backend for an investigation; no auto-fallback or same-app concurrent clients/writers.

## Start and inspect

From the repository root on Termux, after setup/enrollment is already complete:

```sh
access=tools/huckleberry-control/cli.mjs
profile=/absolute/private/enrolled-target.json
session=hb-unique-investigation-id
node "$access" status --profile "$profile"
# Read owner/connection state first; do not take over an existing session.
node "$access" claim --profile "$profile" --session "$session"
node "$access" launch --profile "$profile" --session "$session"
node "$access" inspect --profile "$profile" --session "$session" --out /private/ui-before.json
```

Native Windows uses the same CLI and flags, with PowerShell variables instead:

```powershell
$access = (Resolve-Path './tools/huckleberry-control/cli.mjs').Path
$profilePath = 'C:/private/enrolled-target.json'
$session = "hb-$([guid]::NewGuid().ToString('N'))"
node $access status --profile $profilePath
node $access claim --profile $profilePath --session $session
node $access launch --profile $profilePath --session $session
node $access inspect --profile $profilePath --session $session
```

Use the actual checkout path if working elsewhere. ADB/rish profile transport is explicit, not selected automatically from the host. `launch` uses the launcher API, requires the existing claim and never logs in or chooses a child for you. Missing child/focus/task proof or an unexpected form means stop; the guards are not a general proof that no unsaved work exists.

## Navigation is explicit and at-most-once

Only perform navigation if requested, from the verified Home/Reports screen with no unsaved work. Set caller-supplied IDs once for each intended action; keep them across interruptions. Replace these illustrative values for a new intended action, not merely because a result was lost:

```sh
reportsAction=hb-reports-unique-action-id
homeAction=hb-home-unique-action-id
node "$access" navigate reports --from home --profile "$profile" --session "$session" --action-id "$reportsAction"
node "$access" navigate home --from reports --profile "$profile" --session "$session" --action-id "$homeAction"
# If the reports action was interrupted, reconcile ITS original ID instead:
node "$access" action-status --profile "$profile" --session "$session" --action-id "$reportsAction"
node "$access" inspect --profile "$profile" --session "$session"
```

The two navigate lines illustrate a **verified** round-trip, not a script to continue blindly after an error. If the first command is uncertain, stop before the second. Consumed means at-most-once dispatch, not success/delivery or exactly-once backend work. Observe again; do not generate a fresh ID to repeat an uncertain click. Allow generous host timeouts, e.g. 180 seconds for navigation through slow rish forwarding.

## Evidence and release

`inspect --out` refuses overwrite and saves a private protocol-1 tree with `source: android-accessibility` and `backendCaptured: false`. Windows users must choose a directory protected by their user ACL. Child/UI text and identity fields remain private even if editable values/passwords are redacted.

Trees prune hidden/offscreen content and are bounded. Check `truncated`, `guardComplete`, child/source-tab proof and focus/task identity. An absent row is not proof of record absence. A complete UI tree is not server persistence evidence; this backend currently cannot perform parity-test form writes.

After verified completion/reconciliation:

```sh
node "$access" release --profile "$profile" --session "$session"
```

Release does not erase the consumed-action journal. Do not automatically release/reset after uncertainty or clean up historical test fixtures. A previous task's claim can be recovered only after user confirmation its owner ended; never steal an active claim.

## Acceptance levels

- `npm --prefix tools/huckleberry-control test` is offline/mock testing, not permission to install/run the service or proof of live UI behavior.
- SDK-native CI compilation is build evidence, not Windows/emulator device acceptance. Local Windows default-SDK build and live bridge acceptance remain pending where prerequisites/manual consent are unavailable.
- The repository documents historical managed-profile Termux acceptance of this narrow workflow. Fresh acceptance reports must identify their commit and explicitly separate observations from navigation and backend writes.
- The recorded fresh Termux runner outcome is observation **passed**, full navigation **uncertain/not passed** after independent Reports readback failed. Its terminal run was explicitly reconciled and released without replay. Read the acceptance-status reference linked directly from SKILL.md; do not infer current device availability or retry historical actions from this record.
- Do not use the Windows CDP no-save smoke runner on this backend. The matching checkout provides `tools/huckleberry-control/scripts/termux-smoke.mjs`; read `tools/huckleberry-control/references/termux-acceptance.md` before explicit acceptance.
- The Termux runner defaults to observation-only UI checks, requires `--exclusive-access` and an explicitly enrolled private rish profile, and refuses any existing owner (even its supplied session ID). It never launches/prepares the app or clears a preparation claim. Navigation separately requires `--navigate` and a stable `--action-prefix`, starts only from verified Home, and retains ownership after uncertainty. It is not an ADB/Windows live runner.
- Slow rish transfers can make the complete navigation runner exceed 300 seconds. The acceptance guide discusses a larger whole-run budget, but no timeout is a delivery guarantee. Preserve original IDs and inspect/reconcile; a timed-out run plus manual recovery is not a fresh passed round-trip.
