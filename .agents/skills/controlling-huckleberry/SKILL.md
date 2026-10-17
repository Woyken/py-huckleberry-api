---
name: controlling-huckleberry
description: Controls the Huckleberry Android app through an explicitly selected Windows CDP or shared Windows/Termux accessibility backend. Use for supported app inspection/navigation, Windows CDP test-data entry, or API/Firebase parity investigations with a separately authorized observer. Not for parenting advice, generic Android setup, or bypassing device/profile consent.
compatibility: Node 22.12+. Accessibility requires this repository's tools/huckleberry-control, a manually consented bridge and private enrollment (Windows ADB emulator or Termux/rish managed profile). CDP requires native Windows, an existing SDK/AVD and a debugging-enabled Huckleberry build; initial setup needs npm access.
metadata:
  version: "1.1.0"
---

# Controlling Huckleberry

Use the app UI as the reference implementation. This skill does **not** read Firebase, capture credentials/backend traffic, or establish backend parity by itself.

## Choose the backend before any device command

There are two explicit routes, not one interchangeable driver. The names below are **documentation labels, not `--backend` CLI flags**. Use the user's requested backend/enrolled profile; if ambiguous, ask. Do not infer a fallback from the host OS, an empty tree, or a failed command.

| Route | Entry point | Supported scope |
| --- | --- | --- |
| `accessibility` | `tools/huckleberry-control/cli.mjs` | Shared Windows ADB / Termux rish controller: status, claim, launch, inspect, Home ↔ Reports navigation, action-status, release. |
| `windows-cdp` | This skill's `scripts/huckleberry.mjs` | Existing Windows/emulator WebView control: startup, DOM/ARIA inspection, clicks, form/keypad/text input, screenshots, UI checkpoints and release. |

The shared accessibility backend has **no** generic selectors/clicks, typing, form entry, Save/delete, timers, screenshots or coordinate fallback. Navigation success does not establish those capabilities. The Windows CDP route does not work in Termux; no supported CDP socket was observed in the tested Termux environment.

**Keep one backend for the whole investigation. No mixed-driver workflows or automatic switching are supported.** CDP's host claim and the bridge's device claim are independent, **not a coordinated cross-driver lock**. Do not operate the same app with both tools, or run concurrent clients/writers against a parity capture. This operational restriction is not mechanically enforced across the two backends.

## Accessibility route: Windows or Termux

**Read [the accessibility workflow](references/accessibility.md) first**, including setup/consent, private enrollment, action-ID recovery and evidence limitations. It invokes the existing common controller, not a copy of its logic.

From the repository root, using an already-consented helper and an explicitly enrolled private profile, the command shape is identical on both hosts. Termux example:

```sh
access=tools/huckleberry-control/cli.mjs
profile=/absolute/private/enrolled-target.json
session=hb-unique-investigation-id
node "$access" status --profile "$profile"
# Inspect the owner first; do not take over an existing claim.
node "$access" claim --profile "$profile" --session "$session"
node "$access" launch --profile "$profile" --session "$session"
node "$access" inspect --profile "$profile" --session "$session"
```

ADB remains **emulator-only**; Termux/rish targets only the explicitly enrolled **managed profile**, never the personal Huckleberry copy. Missing permissions/profile visibility means stop, not a policy change or fallback. Building/installing the separate helper and manually enabling its broad accessibility service require explicit consent. This route never boots an AVD or installs/enables anything automatically.

Only if navigation is requested, use the documented `navigate` command with the observed source tab and a unique **retained action ID**. After uncertain output, use the original ID with `action-status` and a fresh inspection. Consumption means at-most-once dispatch, not success. Never generate another ID to repeat an uncertain input.

For explicit Termux acceptance, the matching checkout includes `tools/huckleberry-control/scripts/termux-smoke.mjs` and its `references/termux-acceptance.md` guide. It observes an already-foreground target by default, requires `--exclusive-access`, and refuses any existing claim. Navigation needs separate `--navigate`/retained action-prefix opt-in; the runner never auto-launches/prepares the app. Read the guide before invoking it. A timeout followed by operator recovery is not a passed runner result.

Release with the same accessibility CLI/profile/session **after reconciliation**; do not automatically release/reset after errors. The consumed-action journal survives release.

## Windows CDP route

**Read [Windows CDP setup/recovery](references/windows-cdp.md)** for the preserved PowerShell startup, session and port-forward workflow. Read [CDP controls and recipes](references/controls.md) for selectors, keypad/form input, record readback, screenshots, Node API and no-save smoke tests.

- Select an existing emulator in this skill's `config.json`; defaults are `huck34` / `emulator-5580`. Never select a physical phone. Give cold `start` a 300-second shell timeout.
- Keep one task session and sequential commands within one Windows logon session. Never steal a live claim. Different logon sessions are not covered by the local command mutex.
- Use host `data-qa` selectors (`qa:name`) or explicit CSS. Never force clicks, alter `aria-hidden`, mutate framework state/storage, or replace failed UI actions with backend calls.
- A custom keypad commits to the form, not the tracking record. Verify values/units/event time and exact notes before saving; read back through Reports/Details/Edit. Home can lag, and premium reports may contain demo data.
- Dependencies install outside skill discovery; do not run dependency installation inside this skill directory. The Windows runtime does not need an accessibility bridge.

## API/Firebase parity investigations

**Read [the parity protocol](references/api-parity.md) before any write.** For a parity investigation, require an authorized observer and captured server before-state **before** Save, timer start, edit or delete. Otherwise offer unsaved exploration only. Explicit standalone UI tests are UI-only, not parity tests.

1. Confirm backend capability, exact authorized target/profile/child, observer coverage and quiet starting conditions. Accessibility cannot currently perform form writes; do not attempt them or switch drivers to finish a half-completed investigation.
2. Capture server before-state and backend-specific private UI evidence. Perform one intended supported action; preserve after-state/readback before any cleanup or API comparison.
3. Keep uncertainty explicit. A click acknowledgment, consumed ID, stable UI, truncated tree or absent record is not server persistence evidence. Reconcile lost results before any new input.
4. Preserve evidence first; clean up only exact authorized fixtures as a separate observed operation. **No automatic cleanup that erases before/after evidence.**

Both backends label UI evidence `backendCaptured: false`, but their formats differ: accessibility protocol-1 trees are not CDP checkpoint/DOM snapshots. Preserve source/protocol/coverage and observer timestamps; do not normalize missing fields into a false match. Store files privately in the investigation, not the skill or public PR.

## Evidence and maintenance boundaries

- Read the [acceptance evidence record](references/acceptance-status.md): fresh Termux observation passed, but the fresh full navigation runner remained uncertain; its original claim was reconciled/released. Do not convert historical navigation or recovery into a passed fresh run, or replay it to obtain a green report.
- Shared controller offline/CI checks are not device acceptance. Read the [Windows accessibility live record](references/windows-accessibility-live.md): an explicitly built API-34 helper on Android 14 passed supported inspection/navigation/guards and a fresh cold-process launch case after a narrow readback fix. This does not confer bridge forms/screenshots, every Windows setup, or a passed Termux full runner. CDP's earlier form tests are separate.
- If logged out, wrong child/profile/app, unknown native focus, missing bridge/CDP, or permission denial: stop and report. Do not log in, weaken policy, patch APKs, enable services silently, purchase upgrades, wipe an AVD, or touch historical fixtures.
- A user-level copy of this skill alone does not include `tools/huckleberry-control/`. Accessibility needs the matching repository checkout and bridge protocol; do not use stale/missing external tooling. CDP's bundled scripts remain independently usable on Windows.
- [Evaluation scenarios](references/evaluations.md) distinguish offline, build, live UI and backend evidence. Keep changes in the selected backend's documented scope until separately implemented and tested.
