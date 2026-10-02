# Handoff — App ↔ API Parity Validation

For the next agent continuing this work. Read [`plan.md`](plan.md) for the full design; this file covers
**current state, how to resume, and what to do next**.

Last updated: 2026-10-03.

---

## 1. TL;DR

- Goal: agents drive the real Huckleberry Android app in an emulator, record exactly what it writes to Firestore,
  run the same action via `huckleberry_api`, and diff 1:1 (no extra fields, same types/units/ids/merge semantics).
- **Feasibility proven** with one flow (diaper "both"): login via UI, write capture, wrapper replay, parity diff, cleanup.
- Nothing productized yet: prototype scripts are in [`prototype/`](prototype/). No changes to `src/` or `tests/`.

## 2. What exists

| Path | What |
|---|---|
| `docs/app-parity/plan.md` | Design, findings, tooling spec, scenario backlog, risks |
| `docs/app-parity/prototype/cdp.py` | CDP helper library + CLI (`forward`, `targets`, `eval`, `wait`, `click [--real-touch]`, `type`, `dump`, `screenshot`, `install-recorder`) |
| `docs/app-parity/prototype/login.py` | UI login from `.env` (never prints password); dismisses native popups via uiautomator2 |
| `docs/app-parity/prototype/e2e_diaper_parity.py` | Full diaper parity run: snapshot → app UI action → recorder → wrapper `log_diaper` → diff → delete created docs |
| `docs/app-parity/prototype/avd-huck34-config.ini` | AVD `config.ini` used (copy to `%USERPROFILE%\.android\avd\huck34.avd\config.ini`) |
| `.parity-out/` (gitignored) | Script outputs: snapshots, `app_fs_writes.json`, `parity_report.json`, dumps, screenshots |
| `jadx output latest/` (gitignored) | Decompiled APK **0.9.307**; web bundle in `resources/assets/public/` |

Env overrides for scripts: `HUCK_ADB` (adb path, default `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`),
`HUCK_SERIAL` (default `emulator-5554`), `HUCK_OUT` (default `<repo>/.parity-out`).

## 3. Environment state (dev machine, may be stale)

- Android SDK: `%LOCALAPPDATA%\Android\Sdk` (no `cmdline-tools`/`avdmanager`; AVD was created by hand-writing
  `~/.android/avd/huck34.ini` + `huck34.avd/config.ini`).
- AVD `huck34`: `android-34/google_apis/x86_64` (rootable, debuggable image — **required** for WebView DevTools).
- WHPX acceleration works (Hyper-V stays enabled; do not change system settings).
- Huckleberry **0.9.305** installed (APK: `%USERPROFILE%\Downloads\Huckleberry_+Baby+Tracker_0.9.305_APKPure.apk`),
  logged in with the `.env` test account, child "Sponge".
- A second `adb` exists on PATH (`C:\Android\adb.exe`) — always use the SDK one to avoid server version fights.
- Tools available: `uv`, Node, Java 21, host `frida` (Python 3.10 Scripts). jadx/apkeep were downloaded into a
  session folder that may no longer exist — re-download prebuilt releases if needed (user approved these).
- **Ask the user before installing anything new** (apps, global tools).

## 4. How to resume (commands)

```powershell
$sdk = "$env:LOCALAPPDATA\Android\Sdk"; $adb = "$sdk\platform-tools\adb.exe"
& $adb devices                                   # is emulator-5554 already up?
# If not (cold boot ~170 s; run async and poll sys.boot_completed):
& "$sdk\emulator\emulator.exe" -avd huck34 -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -no-snapshot-save
& $adb -s emulator-5554 wait-for-device; & $adb -s emulator-5554 shell getprop sys.boot_completed
& $adb -s emulator-5554 root
& $adb -s emulator-5554 shell monkey -p com.huckleberry_labs.app 1

cd docs\app-parity\prototype
uv run --no-project --with websockets python cdp.py forward       # finds webview_devtools_remote_<pid>, forwards :9222
uv run --no-project --with websockets python cdp.py eval "location.href"
uv run --no-project --with websockets python cdp.py dump          # agent-friendly element list (tag/id/qa/aria/text/bounds)
# Only if logged out:
uv run --no-project --with websockets --with uiautomator2 --with adbutils python login.py
# Full diaper parity run (WRITES to the test account, then deletes created interval docs):
uv run --with websockets python e2e_diaper_parity.py
```

Notes: re-run `cdp.py forward` after every app restart (socket name contains the PID). The `dump` output is
UTF-8 (icon-font glyphs); stdout is forced to UTF-8.

## 5. Verified facts (don't re-investigate)

- App = Capacitor + Ionic Angular in one WebView; Firestore via **native** SDK through `cordova-plugin-firebasex`
  (`window.FirebasePlugin.setDocumentInFirestoreCollection / mergeDocumentInFirestoreCollection /
  updateDocumentInFirestoreCollection / ...`). DevTools Network shows nothing; the JS-level recorder is the capture channel.
- Captured JS payloads == landed Firestore docs for diaper (no native field injection; `timestamp` arg was `false`).
- Selectors: `data-qa` / ids are stable. Login: `welcome_sign_in`, `login_email` (`hb-email ion-input`),
  `hb-password ion-input`, `login_submit`. Diaper: `diaperPotty_modeSelector_{pee,poo,both}` (needs **real touch**),
  `#diaper_save_button`. Sleep: `hb-timer-button`, `sleep_save_button`. Bottle: `#addFeed_footer_saveButton`,
  `addFeed_bottle_clickAmount`. Home after login: `/tabs/stat-tabs/sessions`, `home_header`, `childSelector`.
- `uiautomator dump` works on the WebView (~2.5 s); uiautomator2 `dump_hierarchy()` ~0.1 s — use for native dialogs.
- Logcat is not a useful capture channel. App Check not enforced; no root detection in use.

## 6. Open findings from the first run

1. **Timezone mismatch** — emulator TZ was UTC+3 (`offset -180`), `.env` `HUCKLEBERRY_TIMEZONE=UTC` (`offset 0`).
   The harness must set emulator TZ = `HUCKLEBERRY_TIMEZONE` (`adb shell cmd alarm set-timezone <TZ>` or
   `setprop persist.sys.timezone`) and assert it before running. Not a wrapper bug.
2. **App side-effect writes** — on diaper save the app also merges `diaper/{cid}.prefs` with full cached prefs
   (`lastPotty`, `reminderV2`) and merges `health/{cid}.prefs` (cached `lastTemperature`, `lastMedication`,
   `lastGrowthEntry` + timestamps). The wrapper writes only `prefs.lastDiaper` + timestamps. Likely benign cache
   sync; decide and document an accept-rule.
3. **Test account dirtied** — `diaper/{cid}.prefs.lastDiaper.offset` is now `0` (written by wrapper). Created
   interval docs were deleted. Restore if it matters.

## 7. Next steps (in order)

1. **Productize infra** (plan §3.1, §8 phase 1): move `prototype/cdp.py` into a real package/tool
   (e.g. `tools/hbdroid/`, deps in a `parity` dependency group in `pyproject.toml`: `websockets`, `uiautomator2`,
   `adbutils`). Add `up` (boot, root, TZ, locale, disable animations), `install`, `login`, `reset`
   (emulator snapshot `logged_in`), recorder auto-reinstall on reload.
2. **Recorder + differ** (plan §3.2–3.3): generic raw snapshot/diff with normalizers (volatile timestamps,
   id format regexes, uuid), compare Firestore value **types**, write method (set/merge/update) and deleted fields.
   Add a wrapper-side recorder (patch `AsyncDocumentReference.set/update/delete`). Unit-test on recorded fixtures.
3. **Scenario runner** (plan §3.4): YAML scenarios + pytest marker `app_parity` (skip unless emulator + `.env`).
4. **Scenarios** (plan §5): diaper variants and potty first, then sleep timer lifecycle, nursing, bottle, solids,
   pump, activities, health, edit/delete, and reverse (API write → app renders).
5. **Agent skill**: `.copilot/skills/huckleberry-app-parity/SKILL.md` documenting commands and rules.
6. Upgrade path: fetch latest APK (`apkeep`), reinstall, rerun all scenarios, diff reports across versions.

## 8. Rules / gotchas

- Follow `AGENTS.md`: schema findings go into `src/huckleberry_api/firebase_types.py` comments; never add values
  not verified by APK or live data; use `uv` for Python.
- Run scenarios **serially** on one emulator; cancel active timers before/after; delete created docs; restore
  touched root prefs.
- Ionic: type into the inner `<input>` of `ion-input`; some controls need `--real-touch`; the diaper save button
  needed a full `pointerdown…click` synthetic event sequence (inline JS in `e2e_diaper_parity.py`, not yet part of
  `cdp.py click` — fold it in when productizing).
- Native permission dialogs live outside the WebView → uiautomator2.
- Never print or commit credentials; `.env*` is gitignored.
- Use only the dedicated test account; keep request volume low.
