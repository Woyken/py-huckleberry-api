# Huckleberry accessibility debugging tools

**Developer tooling only.** This directory is not part of the Python API package,
not an end-user Huckleberry app, and not a product/release APK. It provides one
shared Node.js controller and one small Android AccessibilityService bridge.
Neither modifies Huckleberry's APK or reads its credentials/backend traffic.

The same bridge protocol is used by Windows ADB and Termux/Shizuku `rish`.
No Playwright, WebView debugging socket, HTTP server, screenshots or coordinates
are needed. The shared `.agents/skills/controlling-huckleberry/` skill entry
explicitly routes agents here or to the preserved Windows/CDP implementation;
this tool does not replace that implementation or acquire CDP screenshots/DOM
tooling.

Choose one backend before an investigation. There is no automatic fallback,
generic `--backend` wrapper or mixed-driver workflow. The CDP host lease and
this bridge's device lease are independent, not unified cross-driver locking;
do not operate the same app through both tools concurrently. Protocol 3 retains
the semantic node actions and no longer requires child-name verification; the
skill routes to the common CLI rather than copying it.

## Supported scope

- Explicit target verification, persistent session claim/release, launcher API,
  accessibility inspection and private JSON evidence.
- Semantic node actions: `click`, `long-click`, `set-text`, `scroll-forward`,
  `scroll-backward`, `focus`, `select` and `back`, plus Home ↔ Reports navigation.
  Id/text selectors resolve to the nearest clickable ancestor; paths are also
  supported but are capture-specific. The bridge verifies the focused Huckleberry
  package/task and the enrolled Android user; it does not verify which child is
  selected. Confirm the intended test app/profile before acting; actions remain
  explicit and at-most-once.
- A machine-readable control/selector map in `controls.json`. It covers Home,
  settings, child profile, reports, sleep, feeding, solids, diaper/potty, pump,
  medicine, growth, temperature, activity, milestones and home customization.
- Page objects generated from that catalog: `pages` lists every known route (39
  in the current catalog), `page <id>` returns the live control model for one
  route, and `page-act` resolves a named control (or `page.cN` ref) to one
  semantic action. The same layer is exported as `PageObject` for stateful
  `observe()`/`act(ref)`/`close()` use. A page model reports `present`, `unique`,
  `entryPresent` and guard state; it does not claim the screen is the page just
  because the catalog entry exists.
- No screenshots, coordinate gestures, generic IME/keypad injection or network
  access. Actions can change app/backend state; Save/delete/timer use requires the
  parity safeguards and an authorized observer. Successful UI action does not
  prove Firebase parity.
- ADB is **emulator-only**, with exact serial/AVD and emulator-property checks.
  Termux mode is **managed-work-profile-only**, never personal Android user 0.
  There is no automatic environment, device, profile or driver fallback.

Node.js 22+ is required; the controller has no npm dependencies. The Android
APK has minSdk 28, but task-correlation compatibility is established only for
Android 14+; older targets are unsupported unless separately validated.
The phone path has been exercised on Android 17. Native Windows/Android 14 live
acceptance separately verified the supported workflow with an explicit API-34
local build; see the versioned evidence record below. A different host/build/profile
still needs its own consent/enrollment and acceptance; mocked CI alone is not proof.

## Build the debugging bridge

Requirements: JDK 17+, Android SDK platform `android-35`, build-tools `35.0.0`.
The build uses standard JDK/Android tools directly, not Gradle or downloaded
runtime dependencies. It never installs apps or changes device settings.

```sh
node tools/huckleberry-control/scripts/build-bridge.mjs --sdk /path/to/android-sdk
```

This produces an **unsigned** APK under ignored `android/build/`. To create an
installable development APK, create a private local keystore using `keytool`,
store its password in a private file, and supply both:

```sh
node tools/huckleberry-control/scripts/build-bridge.mjs --sdk /path/to/android-sdk \
  --keystore /private/debug-key.p12 --password-file /private/key-password \
  --out /private/huckleberry-debug-bridge.apk
```

On Windows use native Java/Android tools; quote SDK paths containing spaces.
On Termux/proot the SDK's bundled aapt2/zipalign may be x86 binaries. Supply
compatible ARM64 tools with `--aapt2 /path/to/aapt2 --zipalign /path/to/zipalign`.
An old aapt2 may not understand the API-35 resource table; fail rather than
silently changing SDK/API versions. `--platform` and `--build-tools` explicitly
select other installed SDK versions if you have validated them.

Local ARM64 validation used build-tools 34.0.0's Java D8/apksigner components
with explicitly supplied newer native ARM64 aapt2/zipalign tools. The default
Windows/Linux SDK-native build instead uses build-tools 35.0.0 for API-35 support.

An explicitly selected Windows alternative (`--platform android-34 --build-tools 34.0.0`)
was also compiled/signed/verified and live-tested on Android 14 with JDK 21.
This does not change the API-35 defaults or claim a local default-SDK build. Use an
alternative only with explicit selection and its own compilation/device evidence.

Outputs refuse overwrite. No keystore, password, compiled APK, child data, profile
configuration or evidence should be committed. The helper keeps the prototype
package ID `dev.local.huckleberry.a11yprobe` so an authorized update with the same
local signing key can retain service consent. Do not uninstall an existing helper
just to bypass a signing mismatch: that erases claims and replay protection.

## Install and consent (explicit, once per Android environment)

The helper installs as a **separate app**, not a second Huckleberry copy. Install
only after verifying the exact intended device. Examples below assume the
configured emulator is already running; this tool does not create or boot AVDs.

Windows, with the chosen SDK ADB client (do not restart a shared ADB server):

```powershell
& $adb -s emulator-5580 install --user 0 'C:\private\huckleberry-debug-bridge.apk'
```

Termux: stage the APK in a user-approved shared location, then copy it to a
shell-private path and install **the helper only in user 0** using `rish` and
`pm install --user 0`. Remove your staging copy afterwards. Use `-r` only for an
explicit update of this helper signed by the same key; never uninstall/reinstall
Huckleberry or change a profile restriction as setup/recovery.

The user must manually enable **Huckleberry Debug Bridge** under Android
Accessibility settings. Sideloaded apps may require the user to allow restricted
settings from App info first. Never write secure settings to enable the service.
The service has broad accessibility visibility inherently; enable it only for
authorized debugging and disable it when finished.

The bridge has no Internet permission, screenshot capability, coordinate-gesture
injection or generic input injection beyond accessibility actions. Its
ContentProvider declares DUMP permission and also explicitly accepts only shell
UID 2000. All requests execute serially on the app main thread. Work-profile node
retrieval is performed as the service app, not as shell UID; shell-only
`no_debugging_features` restrictions therefore do not require a policy change
for this path.

## Configure a private target

Copy `profiles/windows.example.json` or `profiles/termux.example.json` to a new
local `.json` file and replace its placeholders. Non-example profiles in that
directory are ignored. `androidUser` is mandatory.

- Windows: choose the existing emulator serial, exact AVD name and Android user.
  Never point this adapter at a physical phone.
- Termux: set the actual **managed profile** ID from Android user metadata. The
  sample user ID is illustrative, not auto-detected or a machine default. Keep
  the personal Huckleberry installation untouched.
- Optional `executable` selects an explicit ADB/rish path; native Windows defaults
  to `adb.exe`, other hosts to `adb`, and Termux mode to `rish`.

Example placeholders intentionally fail validation. Do not put real account
credentials or keystore paths in a profile. Child names and UI evidence are
private even for test accounts.

## Common CLI on either host

The command structure is identical on Windows and Termux. In PowerShell assign
`$profilePath`, `$session` and `$hb` and pass those variables with the same flags.
Below is a POSIX example using a private profile outside the repository:

```sh
hb=tools/huckleberry-control/cli.mjs
profile=/private/huckleberry-debug.json
session=hb-investigation-unique-id

node "$hb" status --profile "$profile"
node "$hb" claim --profile "$profile" --session "$session"
node "$hb" launch --profile "$profile" --session "$session"
node "$hb" inspect --profile "$profile" --session "$session" --out /private/before.json

# Page objects: list routes without a device, inspect one route, then act on a
# named control. page-act re-inspects first; it never batches actions.
node "$hb" pages
node "$hb" page app-shell --profile "$profile" --session "$session"
node "$hb" page-act app-shell --control "Reports tab" --action click \
  --profile "$profile" --session "$session" --action-id hb-page-reports-unique-id

# Each action ID describes one intended action. Never generate a replacement
# merely because a command timed out or its result was lost.
node "$hb" act --action click --id solidsV2SearchInput --profile "$profile" --session "$session" --action-id hb-focus-search-unique-id
node "$hb" act --action set-text --id solidsV2SearchInput --value banana --profile "$profile" --session "$session" --action-id hb-type-search-unique-id
node "$hb" navigate reports --from home --profile "$profile" --session "$session" --action-id hb-reports-unique-id
node "$hb" navigate home --from reports --profile "$profile" --session "$session" --action-id hb-home-unique-id

# After an interruption, check consumption and inspect before any further action.
node "$hb" action-status --profile "$profile" --session "$session" --action-id hb-home-unique-id

node "$hb" release --profile "$profile" --session "$session"
```

`launch` uses Android's launcher API rather than denied `am --user` variants. It
requires an existing matching claim and never logs in or selects a child for you.
If the app/profile is wrong, stop and have the user select the intended test account manually.

After its one launcher request, `launch` may repeat the fresh inspection up to
three times only for the transient `focused_task_not_found` bridge refusal seen
while Android's accessibility window cache catches up with the resumed task.
Each inspection retains identity/ownership guards. Launcher/input dispatch is
never repeated; wrong user/app/focus, permissions, corruption and other failures
still stop immediately. This does not add general navigation retry or diagnose
an unrelated later observation failure.

Other repository scripts can reuse the controller rather than rebuilding the
connection/identity checks. The same code works with either profile transport:

```javascript
import { Controller, loadProfile } from './tools/huckleberry-control/index.mjs';
const controller = await new Controller(await loadProfile(privateProfilePath)).connect();
await controller.claim(sessionId);
const snapshot = await controller.inspect(sessionId);
// If authorized, act/navigate with a unique, retained ID for this intended action:
// await controller.act(sessionId, actionId, { action: 'click', id: 'someId' });
// await controller.navigate(sessionId, 'home', 'reports', actionId);
// Release only after reconciling any interrupted action:
await controller.release(sessionId);
```

For page-aware agent code, open a catalog page and use the refs from the current
observation. Refs are intentionally short-lived: one action invalidates them, and
a new `observe()` is required before the next action.

```javascript
import { Controller, PageObject, loadProfile } from './tools/huckleberry-control/index.mjs';
const controller = await new Controller(await loadProfile(privateProfilePath)).connect();
const page = await PageObject.open(controller, sessionId, 'app-shell');
const model = page.model();
const reports = model.controls.find(control => control.name === 'Reports tab');
if (!reports?.unique) throw new Error('Reports control drifted; propose a catalog fix');
await page.act(reports.ref, 'click', { actionId: retainedActionId });
// Refs are now invalid; call await page.observe() before the next action.
page.close(); // clears local refs; does not release the device claim
await controller.release(sessionId); // only after reconciling the action
```

`page.rawAct({ acknowledgeUnmapped: true, ...request })` is the explicit escape
hatch for a control that is not in the catalog. It still uses the bridge's
focus/task guards; it is not permission to use coordinates or screenshots.
`page.release()` closes the local page object and releases the device claim.

If `page`, `page-act`, or `PageObject.act` reports a missing, ambiguous or stale
control, do not guess another selector or fall back to coordinates. Re-observe;
if the control is still missing, use raw `inspect`/`act` only to discover the new
stable id/text/path, then propose a `controls.json` change (route id, app version,
old/new selector and captured evidence) with a test update.

Inspection prints a compact summary. `--out` saves the versioned visible tree with
private POSIX permissions and refuses overwrite; Windows users should choose a
private directory protected by their user ACL. Hidden/cached and off-screen
subtrees are pruned; their absence is not evidence that a record does not exist.
`truncated` and `guardComplete` distinguish traversal limits from a complete
visible observation. Inconsistent provider visibility causes missing controls
and refusal, not an input fallback.
Password fields are redacted; other editable values, child/UI text and identity fields may be present. Other text is clipped to 256 chars.

## Safety and recovery

- The host verifies exact device/AVD (ADB), managed profile (rish), resumed app,
  Android user, focused window and task. The bridge independently checks the same
  focused/active task and Huckleberry package. Task ID alone is not user proof;
  retain both layers. A task change invalidates the observation.
- Claims live on the device, so Windows/Termux processes share one owner. Use one
  session for one investigation. Do not reuse its ID concurrently or take over an
  old session unless the user confirms its owner has ended. No TTL-based stealing
  or automatic release/reset occurs after errors.
- Claims are cooperative: they cannot stop a human or unrelated automation tool
  from using Android. Focus/refresh checks reduce, but do not eliminate, races
  between observation and dispatch. Do not operate the app manually during a
  command, and never interpret these guards as exactly-once delivery or a sandbox.
- Navigation requires a fully scanned guard tree, the selected source tab and one
  visible/enabled target. Semantic actions require the exact focused task/window
  and one visible, unambiguous target. There
  is no coordinate or arbitrary-first-match fallback. Long histories can
  legitimately exceed the scan limit and cause refusal. Do not run actions while a
  human is using the app; inspect again after any focus change.
- Action IDs are atomically consumed and fsynced on device **before dispatch**,
  including a directory fsync. Protocol 3 also checks the protocol-2 and protocol-1
  journals, so IDs remain consumed across upgrades, release, host loss and app restarts.
  The protocol-3 claim state is new; re-check ownership after upgrading. An
  uncertain/failed action is not replayed. This is at-most-once dispatch, not
  guaranteed delivery or exactly-once UI/backend work.
- Missing/incomplete output is never success. Rish responses are captured in
  shell-private files, compressed, read as bounded unwrapped base64 frames and
  verified against size/SHA-256. Frames carry indices and are transferred in one
  shell session; stream/frame reordering cannot silently corrupt a tree. Only
  exact generated files are removed. ADB uses
  the same protocol and checksummed files through `adb pull`. No shared-storage
  permission or public response file is needed for runtime queries.
- A click acknowledgment is not the postcondition. Navigation readback verifies
  the selected destination; form actions require a fresh inspection/readback.
  The controller retries observations only, never actions. After an interrupted
  command inspect first; do not change the action ID to bypass suppression.
  Release does not erase the action journal.
- Termux startup and output forwarding can be slow. Allow a generous host-tool
  timeout (for example 180 seconds for one navigation command). An external
  timeout is not evidence of failure. `action-status` reports whether the ID was
  consumed, not whether navigation succeeded; pair it with a fresh inspection.
- Do not infer Firebase persistence/parity from this tool. A parity test needs an
  authorized backend observer, before/action/after evidence, exact app readback,
  and cleanup recorded separately. The command surface includes Save/delete/timer
  actions but no observer, fixture marker or automatic cleanup; do not use those
  actions without the documented parity preconditions.

## Opt-in Termux acceptance

The [Termux acceptance guide](references/termux-acceptance.md) documents
`scripts/termux-smoke.mjs`, an opt-in runner over the common controller.
It is **observation-only by default**, requires private rish managed-profile
enrollment and `--exclusive-access`, and refuses existing claims. It does not
launch/prepare the app, install/update the helper, enable services, or change policy.

Only add `--navigate --action-prefix <retained-unique-prefix>` for an explicitly
authorized Home/Reports/Home acceptance. Keep the original session/action IDs after
interruption; never change them merely to replay uncertain input. The runner does
not auto-release on failure. Read the guide's timeout and reconciliation procedure
first, and distinguish observation, interrupted-run recovery, and completed fresh
navigation reports. This Termux-only runner rejects ADB profiles; mocked Windows
execution is not a Windows/emulator live test.

## Tests and implementation boundaries

```sh
npm --prefix tools/huckleberry-control test
```

Offline tests use synthetic data and mocked transports; they do not contact
Android or Firebase. CI is configured to run them on Windows and Linux and compile-check an
unsigned debugging APK. It does not upload or publish that APK. Device consent,
service behavior and Windows live acceptance require separate manual testing.

Shared JavaScript lives in `lib/`: protocol/config, target identity, transport,
controller/workflow and evidence handling. The Android execution boundary is
under `android/`. Platform-specific launcher/transport details stay below the
common CLI. This package is private, dependency-free and outside
`src/huckleberry_api/`; it is not included in Python wheels or release workflows.

Historical common-controller acceptance covered building/signing the helper, explicit update of the
consented helper, claiming/launching/inspecting the enrolled managed-profile app,
Home → Reports → Home, duplicate suppression, rejecting another session and a
changed child under the earlier guard, and releasing without erasing consumed IDs. No screenshots,
tracking writes or profile-policy changes were used. This historical Termux evidence
is not a fresh runner pass or a substitute for the separate Windows live record.

For the new runner, the fresh Termux report is **observation-only passed**, but
**full navigation uncertain/not passed**: independent Reports readback failed after
the common controller verified the first transition. The operator reconciled the
original IDs, read back Reports and explicitly released without replay or Home
navigation. No run or claim from those attempts remained at that handoff.
The exact earlier readback failure is unknown; a later focus refusal is not proof
of its cause. See the [versioned acceptance evidence record](../../.agents/skills/controlling-huckleberry/references/acceptance-status.md)
for provenance and boundaries. Historical navigation, timeout/recovery and CI
success must not be combined into a passed fresh runner round-trip or parity claim.
The earlier protocol-2 control mapping is separate from the runner.

The [Windows accessibility live record](../../.agents/skills/controlling-huckleberry/references/windows-accessibility-live.md)
documents fresh native Windows/Android 14 acceptance after user-enabled service
consent: complete inspection, Home/Reports/Home independent readbacks, competing
claim/enrollment refusal, replay suppression, journal persistence and release. It
also records the initial launch-readback race and the narrow host-only fix, followed
by a clean cold-process launch with one launcher request and two guarded reads.
The helper used an explicit API-34/build-tools-34 build, not a locally performed
API-35 default build. No tracking writes, screenshots or backend observer were used.
This does not change the separate Termux full-runner uncertainty. Protocol 2 was
subsequently exercised live on the enrolled Termux work profile for control
mapping (inspection, scroll, click, set-text and back) without saving records;
that is selector evidence, not a new formal acceptance pass. Protocol 3 changes
the claim and snapshot field set and has not been live-accepted.
