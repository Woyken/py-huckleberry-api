# Huckleberry accessibility debugging tools

**Developer tooling only.** This directory is not part of the Python API package,
not an end-user Huckleberry app, and not a product/release APK. It provides one
shared Node.js controller and one small Android AccessibilityService bridge.
Neither modifies Huckleberry's APK or reads its credentials/backend traffic.

The same bridge protocol is used by Windows ADB and Termux/Shizuku `rish`.
No Playwright, WebView debugging socket, HTTP server, screenshots or coordinates
are needed. The existing Windows/CDP draft skill is not replaced by this tool.

## Supported scope

- Explicit target verification, persistent session claim/release, launcher API,
  accessibility inspection and private JSON evidence.
- **Only Home ↔ Reports navigation**, via accessibility `ACTION_CLICK` on exact
  tab IDs, with expected-child and selected-source-tab checks.
- No generic click, form entry, keypad, Save/delete, timers, screenshot, gesture
  injection or backend mutation commands. Those require separate investigation
  and explicit capability changes; successful navigation does not prove parity.
- ADB is **emulator-only**, with exact serial/AVD and emulator-property checks.
  Termux mode is **managed-work-profile-only**, never personal Android user 0.
  There is no automatic environment, device, profile or driver fallback.

Node.js 22+ is required; the controller has no npm dependencies. The Android
APK has minSdk 28, but task-correlation compatibility is established only for
Android 14+; older targets are unsupported unless separately validated.
The phone path has been exercised on Android 17. Windows command construction is
tested offline; a real Windows/emulator smoke test remains necessary.

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

The bridge has no Internet permission, screenshot/gesture capability or input
methods beyond the two navigation targets. Its ContentProvider declares DUMP
permission and also explicitly accepts only shell UID 2000. All requests execute
serially on the app main thread. Work-profile node retrieval is performed as the
service app, not as shell UID; shell-only `no_debugging_features` restrictions
therefore do not require a policy change for this path.

## Configure a private target

Copy `profiles/windows.example.json` or `profiles/termux.example.json` to a new
local `.json` file and replace its placeholders. Non-example profiles in that
directory are ignored. Both `androidUser` and `expectedChild` are mandatory.

- Windows: choose the existing emulator serial, exact AVD name, Android user and
  authorized test child. Never point this adapter at a physical phone.
- Termux: set the actual **managed profile** ID from Android user metadata and
  the exact test-child name. The sample user ID is illustrative, not auto-detected
  or a machine default. Keep the personal Huckleberry installation untouched.
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

# Each action ID describes one intended action. Never generate a replacement
# merely because a command timed out or its result was lost.
node "$hb" navigate reports --from home --profile "$profile" --session "$session" --action-id hb-reports-unique-id
node "$hb" navigate home --from reports --profile "$profile" --session "$session" --action-id hb-home-unique-id

# After an interruption, check consumption and inspect before any further action.
node "$hb" action-status --profile "$profile" --session "$session" --action-id hb-home-unique-id

node "$hb" release --profile "$profile" --session "$session"
```

`launch` uses Android's launcher API rather than denied `am --user` variants. It
requires an existing matching claim and never logs in or chooses a child for you.
If the child is wrong, stop and have the user choose the authorized child manually.

Other repository scripts can reuse the controller rather than rebuilding the
connection/identity checks. The same code works with either profile transport:

```javascript
import { Controller, loadProfile } from './tools/huckleberry-control/index.mjs';
const controller = await new Controller(await loadProfile(privateProfilePath)).connect();
await controller.claim(sessionId);
const snapshot = await controller.inspect(sessionId);
// If authorized, navigate with a unique, retained ID for this intended action:
// await controller.navigate(sessionId, 'home', 'reports', actionId);
// Release only after reconciling any interrupted action:
await controller.release(sessionId);
```

Inspection prints a compact summary. `--out` saves the versioned visible tree with
private POSIX permissions and refuses overwrite; Windows users should choose a
private directory protected by their user ACL. Hidden/cached and off-screen
subtrees are pruned; their absence is not evidence that a record does not exist.
`truncated` and `guardComplete` distinguish traversal limits from a complete
visible observation. Inconsistent provider visibility causes missing controls
and refusal, not an input fallback.
Passwords and editable values are redacted; other text is clipped to 256 chars.

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
- Navigation requires a fully scanned guard tree, the exact configured child
  button, the selected source tab and one visible/enabled target. There is no
  coordinate or arbitrary-first-match fallback. Long histories can legitimately
  exceed the scan limit and cause refusal. Form checks are defensive, not a
  general proof that no unsaved user work exists; run this only from Home/Reports
  with no user-owned form open.
- Action IDs are atomically consumed and fsynced on device **before dispatch**,
  including a directory fsync. They remain consumed across release, host loss,
  app restarts and upgrades. An uncertain/failed action is not replayed. This is
  at-most-once dispatch, not guaranteed delivery or exactly-once UI/backend work.
- Missing/incomplete output is never success. Rish responses are captured in
  shell-private files, compressed, read as bounded unwrapped base64 frames and
  verified against size/SHA-256. Frames carry indices and are transferred in one
  shell session; stream/frame reordering cannot silently corrupt a tree. Only
  exact generated files are removed. ADB uses
  the same protocol and checksummed files through `adb pull`. No shared-storage
  permission or public response file is needed for runtime queries.
- A click acknowledgment is not the postcondition. The common controller reads
  again and verifies the selected destination. It retries observations only,
  never actions. After an interrupted command inspect first; do not change the
  action ID to bypass suppression. Release does not erase the action journal.
- Termux startup and output forwarding can be slow. Allow a generous host-tool
  timeout (for example 180 seconds for one navigation command). An external
  timeout is not evidence of failure. `action-status` reports whether the ID was
  consumed, not whether navigation succeeded; pair it with a fresh inspection.
- Do not infer Firebase persistence/parity from this tool. Any future parity test
  needs an authorized backend observer, before/action/after evidence, exact app
  readback, and cleanup recorded separately. Current commands do not create such
  fixtures or mutate the backend intentionally.

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

Local acceptance covered building/signing the helper, explicit update of the
consented helper, claiming/launching/inspecting the enrolled managed-profile app,
Home → Reports → Home, duplicate suppression, rejecting another session and a
changed child, and releasing without erasing consumed IDs. No screenshots,
tracking writes or profile-policy changes were used. Windows live acceptance is
still pending; the same request protocol/transport construction is tested offline.
