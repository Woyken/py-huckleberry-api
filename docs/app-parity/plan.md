# Agent-driven App ↔ API Parity Validation — Plan

Goal: let coding agents, **without user interaction**, drive the real Huckleberry Android app,
record exactly what it writes to Firestore, perform the same action through `huckleberry_api`,
and prove the wrapper writes the same documents 1:1 (no extra/"trash" fields, same types,
units, ids, merge semantics, and side-effect writes).

Status: **feasibility proven** on 2026-10-02 (diaper flow, end to end). Items marked ✅ were
executed on a dev machine; everything else is planned.

---

## 1. Key findings (evidence-based)

| Topic | Finding | Status |
|---|---|---|
| Emulator | Android Studio emulator, AVD `huck34` from `system-images/android-34/google_apis/x86_64`, WHPX acceleration, headless (`-no-window`). Cold boot ≈170 s. | ✅ |
| Root | `google_apis` image → `adb root` works (`uid=0`). `google_apis_playstore` images can't `adb root` — avoid. | ✅ |
| APK | APKPure artifact is universal (`arm64-v8a, armeabi-v7a, x86, x86_64`) → runs natively on x86_64. Installs with `adb install -r -g`. | ✅ |
| App architecture | Capacitor + Ionic Angular; UI in one WebView (`http://localhost/...`). Web bundle: `jadx output latest/resources/assets/public/`. | ✅ |
| Firestore SDK | **Native** Android Firebase SDK through Cordova plugin `cordova-plugin-firebasex` (`window.FirebasePlugin.*`), not the Firebase JS SDK → Firestore traffic is **not** visible in DevTools Network. | ✅ |
| WebView debugging | App calls `WebView.setWebContentsDebuggingEnabled(false)`, but on a **debuggable system image** (google_apis emulator) the socket `@webview_devtools_remote_<pid>` is still exposed → full CDP access (DOM + JS runtime). | ✅ |
| Accessibility tree | `adb shell uiautomator dump` exposes WebView nodes (text, resource-id = DOM id, bounds), ~2.2–2.8 s/dump. `uiautomator2` `dump_hierarchy()` ≈0.05–0.1 s. Useful fallback; CDP DOM is richer. | ✅ |
| Stable selectors | Bundle uses `data-qa` attributes and ids: `welcome_sign_in`, `login_email`, `login_submit`, `diaperPotty_modeSelector_{pee,poo,both}`, `diaper_save_button`, `hb-timer-button`, `sleep_save_button`, `addFeed_footer_saveButton`, `addFeed_bottle_clickAmount`, … | ✅ |
| Write capture | Wrapping `window.FirebasePlugin` Firestore methods via CDP `Runtime.evaluate` records **intent-level** writes: method (`set`/`merge`/`update`/`delete`), collection path, docId, full payload. Captured payload == landed Firestore doc (no native field injection; `timestamp` flag `false`). | ✅ |
| Login | Email/password via UI works. App Check library present but not enforced; root detection not used. | ✅ |
| Logcat | Only Firestore mapper warnings; not a useful capture channel. | ✅ |
| MITM | Not needed — JS-bridge capture is better (pre-serialization, includes merge semantics). Frida kept as fallback for native-only writes. | — |

### First parity result (diaper "both")

| Aspect | App | Wrapper | Verdict |
|---|---|---|---|
| interval keys | `start, mode, offset, lastUpdated` | same set | ✅ no extra keys |
| `mode` / `start` | `"both"` / `1790969501.595` | same | ✅ |
| interval id | `{ms}-{20 hex}` | same format | ✅ |
| `offset` | `-180` (emulator TZ UTC+3) | `-0.0` (`.env` TZ = UTC) | ⚠ env mismatch, not a code bug — emulator TZ must equal `HUCKLEBERRY_TIMEZONE` |
| `offset` stored type | double (bridge stores JS numbers as double) | double | ✅ |
| root `diaper/{cid}` | `merge` of full `prefs` incl. `lastDiaper`, `lastPotty`, `reminderV2`, timestamps | `set(merge=True)` of `prefs.lastDiaper` + timestamps | ✅ same landed fields |
| side-effect writes | app also merges `health/{cid}.prefs` (re-writes cached lastTemperature/lastMedication/lastGrowthEntry + timestamps) | none | ℹ app refresh side effect — accept explicitly |

These rows are the kind of drift the harness surfaces automatically.

---

## 2. Architecture

```
 ┌───────────── agent (Copilot CLI) ─────────────┐
 │  scenario YAML  →  parity runner (pytest)      │
 └──────┬──────────────────────┬─────────────────┘
        │ UI actions            │ API actions + reads
        ▼                       ▼
 ┌──────────────┐        ┌───────────────────────┐
 │ hbdroid CLI  │        │ huckleberry_api        │
 │ adb + CDP    │        │ + raw Firestore client │
 │ + uiautomator2│       └──────────┬────────────┘
 └──────┬───────┘                   │
        ▼                           ▼
  Emulator (huck34, rooted)  ──►  Firestore (project simpleintervals, test account)
  WebView ← write recorder (wraps window.FirebasePlugin)
```

Three evidence channels per action, compared by a deterministic differ:

1. **Intent** – recorded `FirebasePlugin` calls (method, path, docId, payload, merge flag).
2. **Landed state** – raw Firestore snapshots before/after (root docs + newest N interval docs) with the raw
   client. Models use `extra="ignore"`, so typed models would hide extra fields — always diff raw dicts.
3. **Wrapper** – the same scenario via `HuckleberryAPI`, with its own snapshots plus a recorder around the
   wrapper's Firestore calls (patch `AsyncDocumentReference.set/update/delete`) for intent parity.

---

## 3. Tooling to build (deterministic helpers for agents)

Agents should not reason from pixels; they call small deterministic commands with JSON output.

### 3.1 `hbdroid` — device/app control CLI (Python, run with `uv`)
Productized from the prototype `cdp.py`/`login.py` (§7).

| Command | Purpose |
|---|---|
| `hbdroid up` | Create AVD if missing, boot headless, wait for `sys.boot_completed`, `adb root`, set timezone to `HUCKLEBERRY_TIMEZONE` (`adb shell cmd alarm set-timezone <TZ>`), locale `en-US`, disable animations (`settings put global {window,transition,animator}_animation_scale 0`). Idempotent. |
| `hbdroid install [--apk path]` | Install/upgrade APK; record `versionName` (`dumpsys package`). |
| `hbdroid login` | Log in from `.env` (never prints password); dismiss native permission dialogs with uiautomator2. |
| `hbdroid reset` | `pm clear` + relaunch, or load emulator snapshot `logged_in` (`adb emu avd snapshot save/load`) for fast resets. |
| `hbdroid dump [--native]` | **Agent-friendly accessibility tree**: JSON list of visible interactive elements `{tag, id, qa, aria, text, role, bounds, disabled}` from the DOM via CDP; `--native` uses the uiautomator2 hierarchy (system dialogs, keyboard). |
| `hbdroid click <selector> [--real-touch]` | DOM click; `--real-touch` maps element rect × devicePixelRatio + WebView offset → `adb shell input tap` (needed for some Ionic controls). |
| `hbdroid type <selector> <text>` | Targets the inner native `<input>` of `ion-input`, dispatches `input`/`change`. |
| `hbdroid wait <selector> / --url / --text` | Poll with timeout. |
| `hbdroid nav <route>` | Angular router navigation to deep routes (faster, fewer flaky taps). |
| `hbdroid recorder install / read / clear` | Write recorder (below); auto-reinstall after reloads. |
| `hbdroid screenshot <name>` | `adb exec-out screencap -p`, for run reports only. |

### 3.2 Write recorder (JS injected via CDP)
- Wrap every Firestore method on `window.FirebasePlugin`: `setDocumentInFirestoreCollection`,
  `mergeDocumentInFirestoreCollection`, `updateDocumentInFirestoreCollection`, `addDocumentToFirestoreCollection`,
  `deleteDocumentFromFirestoreCollection`, and any batch/transaction methods present (check
  `plugins/cordova-plugin-firebasex/www/firebase.js` for each app version).
- Push `{method, collection, docId, data: structuredClone(data), merge, timestamp, ts}` into `window.__fsWrites`.
- Install lazily (poll / `defineProperty`) and via `Page.addScriptToEvaluateOnNewDocument`.
- Fallback for native-only writes (if any appear): Frida hook on `FirestoreWrapper` / `FirebasePlugin.java`
  (`frida-server` matching the host `frida` version, pushed to `/data/local/tmp`).

### 3.3 Firestore snapshot + differ (Python, reuses `huckleberry_api` auth)
- `snapshot(paths, newest_n)` → raw dicts that keep Firestore types (int vs double, Timestamp, null vs missing).
- `diff(before, after)` → created/changed/deleted docs and dotted field paths.
- `parity(app_effect, wrapper_effect, rules)` with **normalizers**:
  - Volatile fields: require presence + type, ignore value: `lastUpdated`, `local_timestamp`,
    `prefs.timestamp.seconds`, doc ids (format regex `^\d{13}-[0-9a-f]{20}$`), timer `uuid` (format only).
  - Compare exactly: key sets, value types, enums, units, `offset`/`end_offset`, nested shapes, write method
    (`set` vs `merge` vs `update`), deleted fields.
  - Per-scenario allow-list for known/accepted differences, each with a reason.
- Output `parity_report.json` + Markdown summary; non-zero exit on unexpected drift.

### 3.4 Scenario definitions (`tests/app_parity/scenarios/*.yaml`)
Declarative, so agents can add flows without writing code:

```yaml
id: diaper_both_default
tracker: diaper
ui:
  - click: '[data-qa="diaperPotty_modeSelector_both"]'
    real_touch: true
  - click: '#diaper_save_button'
  - wait_writes: 2
api:
  call: log_diaper
  args: {mode: both, start: "$app.start"}   # values taken from the app's recorded write
watch:
  roots: [diaper/{cid}, health/{cid}]
  intervals: [diaper/{cid}/intervals]
accept:
  - path: health/{cid}
    reason: "app re-merges cached health prefs on any save"
```

---

## 4. End-to-end flow (one scenario)

1. `hbdroid up`, `hbdroid login` (or load `logged_in` snapshot). Assert emulator TZ == `HUCKLEBERRY_TIMEZONE`.
2. Resolve child (`get_user()`). Ensure **no active timers** (API `cancel_*`) so runs are independent.
3. `snapshot(watch)` → `S0`; `recorder clear`.
4. Execute UI steps → read recorder → `W_app`; poll until writes landed → `S1`.
5. Derive API parameters from `W_app` (start, amounts, …) so the call is semantically identical.
6. Run the API call with the wrapper recorder → `W_api`; snapshot → `S2`.
7. `parity(W_app, W_api)` and `parity(diff(S0,S1), diff(S1,S2))`.
8. Cleanup: delete created interval docs (ids recorded); restore touched root-prefs paths from `S0`.
9. Emit report; attach screenshot + `hbdroid dump` on failure.

Timers also run in reverse (wrapper starts → app must render and stop it correctly), which validates **reads**.

---

## 5. Scenario backlog (priority)

| # | Flow | Paths | Notes |
|---|---|---|---|
| 1 | Diaper pee/poo/both/dry, color/consistency/rash/notes | `diaper/*` | ✅ "both" prototyped |
| 2 | Potty | `diaper/*` | `howItHappened`, `isPotty` |
| 3 | Sleep timer start/pause/resume/complete/cancel; manual sleep | `sleep/*` | ms vs s, `uuid`, `timerStartTime: null` |
| 4 | Nursing timer + side switch; manual nursing | `feed/*` | `DELETE_FIELD` on completion |
| 5 | Bottle (ml/oz, types) | `feed/*` | `prefs.bottleType/Amount/Units` |
| 6 | Solids (curated, custom food, reactions) | `feed/*`, `types/*` | |
| 7 | Pump timer + manual | `pump/*` | `endTime` delete on resume |
| 8 | Activity timers + switch | `activities/*` | shared `uuid` |
| 9 | Growth, temperature, medicine (type + log) | `health/*` | `_id`, `multientry_key`, `isNight` |
| 10 | Edit / delete existing entries | all | update semantics |
| 11 | Reverse: API write → app renders correctly | all | read-side validation |

---

## 6. Agent operating model (fleet)

- **Orchestrator**: owns infra and the single emulator; runs scenarios **serially** (one device = one UI actor;
  Firestore state is shared per child).
- **Scenario-author subagents** (parallel, no device access): grep the decompiled bundle for selectors
  (`data-qa`) and payload builders (`sanitize*Data`, `add*Interval`), draft scenario YAML + expected writes.
- **Runner**: executes scenarios via `hbdroid`/pytest, produces reports.
- **Analyst subagent**: classifies each drift (wrapper bug / env mismatch / app side effect / volatile) with
  evidence (recorded payload + bundle snippet) and proposes changes to `firebase_types.py` / `api.py`
  following `AGENTS.md` (never add unverified values).
- Multi-device parallelism (more AVDs on `-port 5556…`, separate test children) is possible but optional.
- On UI failure: `hbdroid dump` → choose selector → retry; never coordinates from screenshots.

---

## 7. Prototype artifacts and commands

Prototype scripts are committed in [`prototype/`](prototype/) (see [`HANDOFF.md`](HANDOFF.md)): `cdp.py` (CDP helper +
CLI: `forward|targets|eval|wait|click [--real-touch]|type|dump|screenshot|install-recorder`), `login.py`,
`e2e_diaper_parity.py`, `avd-huck34-config.ini`. Run output goes to the gitignored `.parity-out/`.

```powershell
$sdk = "$env:LOCALAPPDATA\Android\Sdk"; $adb = "$sdk\platform-tools\adb.exe"
# AVD huck34 created manually (no cmdline-tools): ~/.android/avd/huck34.ini + huck34.avd/config.ini
& "$sdk\emulator\emulator.exe" -avd huck34 -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -no-snapshot-save
& $adb -s emulator-5554 root
& $adb -s emulator-5554 install -r -g "<Huckleberry.apk>"
& $adb shell cat /proc/net/unix | Select-String webview_devtools_remote
& $adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
curl.exe http://localhost:9222/json
uv run --no-project --with websockets --with uiautomator2 --with adbutils python login.py
uv run --with websockets python e2e_diaper_parity.py
```

---

## 8. Implementation phases

1. **Infra** — `tools/hbdroid/` (or a `parity` dependency group in `pyproject.toml`: `websockets`,
   `uiautomator2`, `adbutils`); `hbdroid up/install/login/reset`, TZ + animation setup, snapshot reuse.
2. **Recorder + differ** — JS recorder, wrapper-side recorder, raw snapshot/diff/parity with normalizers;
   offline unit tests on recorded fixtures.
3. **Scenario runner** — YAML schema, pytest collection (`-m app_parity`, skipped unless emulator + `.env`).
4. **Scenarios** — backlog §5 in order; findings documented in `firebase_types.py`.
5. **Agent skill** — `.copilot/skills/huckleberry-app-parity/SKILL.md` (commands, rules, fleet model).
6. **APK upgrades** — `apkeep` latest → install → rerun all scenarios → diff reports between app versions.

## 9. Risks / gotchas

- **Timezone**: emulator TZ must equal `HUCKLEBERRY_TIMEZONE`, else `offset` diffs (seen in prototype).
- **Ionic controls**: `ion-input` needs inner `<input>` events; some controls (diaper mode) need real touch;
  save buttons needed a full pointer/mouse/touch/click event sequence.
- **WebView debugging relies on a debuggable system image** — keep `google_apis` (non-Play) images. If blocked
  in future: Frida hook forcing `setWebContentsDebuggingEnabled(true)`, or uiautomator2 accessibility fallback.
- **Native dialogs** (notification permission) are outside the WebView → uiautomator2.
- **App side-effect writes** (e.g. `health` prefs re-merge on diaper save) — decide explicitly per scenario.
- **Shared mutable state**: run serially, cancel timers before/after, delete created docs, restore root prefs.
- **Numeric types**: compare raw Firestore types (int vs double), not only values.
- **App Check / Play Integrity**: library present but not enforced today; re-check on APK upgrades.
- Use the dedicated test account only and keep request volume low.
