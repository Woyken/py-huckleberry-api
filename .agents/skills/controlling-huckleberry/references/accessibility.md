# Shared accessibility workflow

This is the `accessibility` route: the existing `tools/huckleberry-control/` controller and consented Android bridge. It works through explicitly enrolled Windows ADB emulators or Termux/Shizuku rish managed profiles. It is developer tooling, not part of the Python API product. Protocol 2 adds semantic node actions (click, long-click, set-text, scroll, focus, select, back) while retaining the same enrollment, task/focus guards and at-most-once action journal. It still has no screenshots, coordinate gestures or network access.

## Setup boundary (read before device access)

Read `tools/huckleberry-control/README.md` in the matching repository checkout for the complete build/install/manual-consent and enrollment procedure. The skill does not bundle the helper APK, a signing key, a profile, or a backend observer. A standalone user-profile skill copy is insufficient for this route.

Use an existing private profile derived from the repository's example for the explicitly authorized target. Placeholders intentionally fail. Never invent an Android user, expected child, serial or AVD, and never commit the real profile.

- ADB targets an exact existing **emulator**; never a physical phone.
- Termux/rish targets an enrolled **managed profile**; never personal Android user 0. The helper itself may be installed in the parent user as documented; that does not authorize controlling its personal Huckleberry copy.
- The helper is a separate app with broad accessibility visibility inherently. Its manual enablement/installation/update requires explicit consent. Do not write secure settings, remove managed-profile restrictions, or patch Huckleberry as recovery.
- This controller does not boot AVDs, install apps, enable services or select a child. It has semantic selectors and actions, but no screenshots, coordinate gestures or generic input injection. Actions can change app/backend state; Save/delete/timer controls are available and require the parity safeguards below.
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

## Page objects and drift

`controls.json` is exposed as page objects so an agent does not need to copy raw
selectors. `pages` is offline; the other commands use the same claim/profile as
`inspect`:

```sh
node "$access" pages
node "$access" page app-shell --profile "$profile" --session "$session"
node "$access" page-act app-shell --control "Reports tab" --action click \
  --profile "$profile" --session "$session" --action-id hb-page-reports-unique-id
```

`page` returns the route's controls with `ref`, `present`, `unique`, declared
`actions` and `mutates`/`warn` metadata, plus `entryPresent`, `missing` and
`screen.childVerified`. It does not assert that the screen is that page; check the
entry/control matches and identity before acting. `page-act` re-inspects, resolves
one unique selector and dispatches one declared action. It accepts `--control
"<name>"` or `--ref <page.cN>`; refs are a convenience and are revalidated against
the fresh inspection, not persisted across CLI calls. The JavaScript API's
`PageObject.observe()`/`act(ref)` makes refs snapshot-scoped: one action or
`close()` invalidates them.

In the JavaScript API, `rawAct({acknowledgeUnmapped: true, ...})` is the explicit
unmapped-control escape hatch. It still requires the normal bridge
focus/task/child guards and does not add coordinates or screenshots.

**Control drift rule:** if a page/control is missing, ambiguous, or a ref is stale,
stop the intended action. Re-observe; if it still fails, use raw `inspect`/`act`
only to discover the new stable selector. Then propose a `controls.json` update
with the route id, app version, old/new selector and private evidence, plus a
page-object test. Do not guess another selector, use coordinates, or silently
substitute a similar label. Report the drift even when the read-only discovery
succeeds.

## Actions are explicit and at-most-once

Use the [control catalog](https://github.com/Woyken/py-huckleberry-api/blob/feat/huckleberry-control-skill/tools/huckleberry-control/controls.json) as a selector map, then re-inspect the live tree before acting. Prefer stable ids and unique text labels; paths are capture-specific. The bridge resolves id/text selectors to the nearest clickable ancestor for `click`/`long-click`.

Before the first `click`, `long-click` or `set-text`, inspect a Home/Reports screen until `childVerified` is true. The bridge caches that proof for the exact task because forms do not expose the child label. A new claim/release, observing the Child tab or another child, clicking the text-selected child switcher, an incomplete scan, or a new Huckleberry task clears it. Inspecting a form reports the cached proof and does not discard it because the child label is hidden. Inspecting the Child tab reports false and clears it. If an action returns `child_not_verified`, go back to a verified Home/Reports view and inspect again; do not bypass the guard. Home/Reports tab **clicks** made with `act --action click` are exempt, so they can leave the Child tab even after verification is cleared; `navigate` still requires `childVerified`, so use the tab click rather than `navigate` for that recovery. Private-use icon glyphs such as close/back may not survive shell text transport; use a stable id or the captured node path for those buttons.

Only perform navigation if requested, from the verified Home/Reports screen with no unsaved work. State-changing actions require an inspected, unambiguous target and the parity/observer preconditions; they may occur on a form or modal. Do not use a global `back` from a form root, because it can leave Huckleberry. Set caller-supplied IDs once for each intended action; keep them across interruptions. Replace these illustrative values for a new intended action, not merely because a result was lost:

```sh
reportsAction=hb-reports-unique-action-id
homeAction=hb-home-unique-action-id
node "$access" navigate reports --from home --profile "$profile" --session "$session" --action-id "$reportsAction"
node "$access" navigate home --from reports --profile "$profile" --session "$session" --action-id "$homeAction"
# Example semantic action on a verified form control:
node "$access" act --action set-text --id solidsV2SearchInput --value banana --profile "$profile" --session "$session" --action-id hb-search-unique-action-id
# If any action was interrupted, reconcile ITS original ID instead:
node "$access" action-status --profile "$profile" --session "$session" --action-id "$reportsAction"
node "$access" inspect --profile "$profile" --session "$session"
```

The navigate lines illustrate a **verified** round-trip, not a script to continue blindly after an error. If an action is uncertain, stop. Consumed means at-most-once dispatch, not success/delivery or exactly-once backend work. Observe again; do not generate a fresh ID to repeat an uncertain click. Allow generous host timeouts, e.g. 180 seconds for navigation through slow rish forwarding.

For forms, inspect before and after each action. Use in-app close/back controls where possible; global `back` from a root page can leave Huckleberry. Do not use Save, delete or timer start during exploration unless the parity preconditions in `api-parity.md` are met.

## Evidence and release

`inspect --out` refuses overwrite and saves a private protocol-2 tree with `source: android-accessibility` and `backendCaptured: false`. Windows users must choose a directory protected by their user ACL. Password fields are redacted, but other editable values, child/UI text and identity fields may be present; treat every tree as private.

Trees prune hidden/offscreen content and are bounded. Check `truncated`, `guardComplete`, child/source-tab proof and focus/task identity. An absent row is not proof of record absence. A complete UI tree is not server persistence evidence. Semantic form actions are available, but UI success still does not prove Firebase parity.

After verified completion/reconciliation:

```sh
node "$access" release --profile "$profile" --session "$session"
```

Release does not erase the consumed-action journal. Do not automatically release/reset after uncertainty or clean up historical test fixtures. A previous task's claim can be recovered only after user confirmation its owner ended; never steal an active claim.

## Acceptance levels

- `npm --prefix tools/huckleberry-control test` is offline/mock testing, not permission to install/run the service or proof of live UI behavior.
- Protocol 2 control mapping was exercised live on the enrolled work profile in this worktree: inspection, scrolling, clicks, `set-text`, `back`, modal/page navigation and form controls were used without saving. Raw trees stayed private. This is control/selector evidence, not a formal acceptance run or backend parity.
- SDK-native CI compilation is build evidence, not Windows/emulator device acceptance. The Windows live record linked directly from SKILL.md separately validates an explicit API-34 build on Android 14 and the supported cold-launch/navigation/guard workflow after manual consent. The default API-35 local build was not performed; each other target still needs its own setup/consent and acceptance.
- The repository documents historical managed-profile Termux acceptance of this narrow workflow. Fresh acceptance reports must identify their commit and explicitly separate observations from navigation and backend writes.
- The recorded fresh Termux runner outcome is observation **passed**, full navigation **uncertain/not passed** after independent Reports readback failed. Its terminal run was explicitly reconciled and released without replay. Read the acceptance-status reference linked directly from SKILL.md; do not infer current device availability or retry historical actions from this record.
- Do not use the Windows CDP no-save smoke runner on this backend. The matching checkout provides `tools/huckleberry-control/scripts/termux-smoke.mjs`; read `tools/huckleberry-control/references/termux-acceptance.md` before explicit acceptance.
- The Termux runner defaults to observation-only UI checks, requires `--exclusive-access` and an explicitly enrolled private rish profile, and refuses any existing owner (even its supplied session ID). It never launches/prepares the app or clears a preparation claim. Navigation separately requires `--navigate` and a stable `--action-prefix`, starts only from verified Home, and retains ownership after uncertainty. It is not an ADB/Windows live runner.
- Slow rish transfers can make the complete navigation runner exceed 300 seconds. The acceptance guide discusses a larger whole-run budget, but no timeout is a delivery guarantee. Preserve original IDs and inspect/reconcile; a timed-out run plus manual recovery is not a fresh passed round-trip.
