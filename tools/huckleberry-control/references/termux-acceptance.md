# Termux accessibility acceptance (UI-only)

This is the Termux-owned acceptance slice for the shared `accessibility` route.
It uses the existing `index.mjs` controller, protocol 3 and consented Android
bridge without copying transport/identity/selector logic or changing that protocol.
It is a developer test, not a Huckleberry product or Firebase observer.

## Preconditions and consent

Read `tools/huckleberry-control/README.md` and the skill's selected-backend guide
first. A complete matching repository checkout and Node.js 22+ are needed; a
standalone skill copy does not include the common controller/bridge.

- Use the explicitly authorized private **rish managed-profile enrollment**.
  The runner refuses ADB profiles and personal Android user 0. Never infer the
  target from a package name, the host OS, or an example user ID.
- Shizuku must already provide Android shell access to Termux. This is not root.
  Work-profile shell debugging restrictions need not be removed: the consented
  AccessibilityService, not shell-based UI Automator, retrieves the nodes.
- The separate helper must already be installed and manually enabled by the
  user. A missing/disconnected service is a blocker, not permission to enable it,
  write secure settings, change policy or install/update an APK automatically.
- Local ARM64 building may need explicit native aapt2/zipalign overrides, as
  documented by the common build tool. A build is not device acceptance. Signing
  material, SDK locations and real profiles stay private.
- Use **one backend for the entire investigation**. CDP host claims and bridge
  device claims are independent. `--exclusive-access` is the operator's explicit
  acknowledgment that no human/other client/backend is using the app; it is not a
  tested cross-driver lock or a mechanism that stops unrelated tools.
- No user-owned form or unsaved work may be open. Do not touch historical
  fixtures, subscriptions, credentials, child selection or unrelated settings.

The runner never launches or prepares the app. Have the user foreground the
enrolled app on Home/Reports, or prepare it through the already-authorized common
CLI with a separate session. Check that setup succeeded and release that
preparation claim before invoking the runner. If setup is uncertain, stop and
reconcile it; do not take over its claim to run this test.

## Observation-only default

From the repository root, after verifying the private profile and exclusive
access, choose a unique session ID once for this intended acceptance run:

```sh
profile=/absolute/private/enrolled-target.json
session=hb-observation-unique-run
node tools/huckleberry-control/scripts/termux-smoke.mjs \
  --profile "$profile" --session "$session" --exclusive-access
```

This performs target/service/capability checks, refuses **any** existing bridge
owner (even the supplied session ID), verifies foreground identity, claims a new
session, reads a complete visible Home/Reports observation and releases only
after successful verification. Claim/release is private debug metadata, not app
UI input. No navigation, launch, install, screenshot, bridge action or tracking
mutation is performed by default. An unknown tab, wrong evidence source or
incomplete/truncated observation blocks acceptance.

## Explicit Home/Reports round-trip

Only when navigation is authorized and the app is already on verified **Home**:

```sh
session=hb-navigation-unique-run
prefix=hb-stable-unique-action-prefix
node tools/huckleberry-control/scripts/termux-smoke.mjs \
  --profile "$profile" --session "$session" --exclusive-access \
  --navigate --action-prefix "$prefix"
```

Retain these caller-supplied values across interruptions. The action IDs are
exactly `<prefix>-reports` and `<prefix>-home`. Both must be unused before either
navigation is attempted. The runner invokes each transition once through the
common controller and independently reads its postcondition before continuing.
It never resets an unexpected start tab to Home or generates replacement IDs.

The default report is a small allowlisted JSON object: mode, phase, reason code,
boolean checks, ownership-state category and explicit UI-only/no-backend-capture
labels. It contains no child/account text, raw node tree, private path, Android
user/task/device identifier, session/action ID or raw exception message. It does
not automatically write or upload any file. Review even redacted output before
posting it; never attach the private profile or original controller tree.

## Failure and recovery

Exit code 0 means this specific run completed and released successfully. Nonzero
means blocked or uncertain; read `phase`, `reason` and `claimState`.

- Before claim, a refusal leaves ownership alone. A claim-call error may mean an
  unknown ownership outcome, not a free target.
- After a confirmed claim, any failure retains it. There is deliberately no
  `finally` block that releases ownership, navigates Home or cleans up fixtures.
- Any failure after invoking navigation is conservatively **uncertain**. No
  later navigation is attempted. A lost release reply leaves ownership unknown.
- Use the unchanged common CLI with the **original session/action IDs** to read
  `status`, `action-status` and a fresh `inspect`. Consumed means dispatched or
  uncertain, not successful. A missing record/control is not proof of absence.
- Do not simply rerun the runner with a new session/prefix. It intentionally
  refuses existing claims and consumed IDs rather than resuming a prior run.
  Release through the common CLI only after reconciling the run and confirming
  its owner ended. Journal retention is intentional; never uninstall/reset the
  helper to clear replay protection.

Rish forwarding can be slow. The full round-trip can perform seven observations
(including the common controller's own pre/post checks), plus claim and action-ID
checks. A 300-second host timeout was observed to interrupt a run after Reports
was reached but before Home was attempted. For a slow host, allow a larger
whole-run budget (for example 600 seconds), not an input-retry loop. Even that is
not a delivery guarantee. An external timeout is not proof that a command failed;
reconcile the original IDs and actual UI before deciding on any further input.
CDP is not a supported Termux fallback here.

## Evidence levels and cross-host matrix

Use the same assertions when reviewing the Windows agent's shared-backend
acceptance, but **do not run this Termux-only runner with an ADB profile**:

- **Offline/mock:** option/profile/capability refusal, existing-owner refusal,
  missing focus/source tab, truncation, used action IDs, no release or
  retry after uncertainty, and report sanitization. Ordinary `npm test` does not
  contact a device.
- **Build:** explicitly selected SDK/native tools compile/sign the helper. This
  is not installation, service consent or live UI evidence.
- **Live observation:** enrolled target identity, connected service, vacant claim,
  target identity, complete visible evidence, known Home/Reports tab and release.
- **Live navigation:** all preceding conditions, unused retained IDs, Home →
  Reports → Home with independent postcondition observations, then release.
- **Not established by this runner:** form actions, set-text/keypad behavior,
  Save/delete/timers, screenshots, a coordinated CDP/bridge lock, Firebase
  persistence or app/API parity. Protocol 3 exposes semantic actions including
  `set-text`, but acceptance of those actions is a separate UI-control exercise,
  not this navigation runner. No backend observer runs in this test.

For a handoff, report the source branch/commit, exact test mode, mocked counts,
redacted fresh live result or explicit blocker, and scope/consent limitations.
Distinguish fresh runs of this runner from historical acceptance of the common
controller. Do not claim Windows device success from a Termux run or vice versa.
Public PR comments may include code SHAs and sanitized assertions, never private
enrollment, record details, raw trees, screenshots or identifying device data.
