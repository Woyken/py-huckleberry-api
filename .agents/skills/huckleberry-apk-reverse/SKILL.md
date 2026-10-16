---
name: huckleberry-apk-reverse
description: Analyzes Huckleberry Android APKs through acquisition, JADX decompilation, JavaScript deobfuscation, and schema evidence validation. Use when reverse engineering the Huckleberry app, inspecting APK resources or web chunks, or verifying Firebase payload values against app code.
---

# Huckleberry APK Reverse Engineering Skill

## Purpose

Use this skill for end-to-end reverse engineering of Huckleberry Android APKs.

- Download and version APK artifacts
- Decompile app resources and source with JADX
- Triage Java, JS, manifest, and resource evidence
- Deobfuscate/prettify web chunks when analyzing obfuscated releases (0.9.280+)

## Scope

This skill covers:
- APK acquisition and version pinning
- JADX decompilation workflow
- Static analysis strategy for `sources/`, `resources/`, and web chunks
- JS deobfuscation pipeline for `resources/assets/www/*.js`
- Evidence validation patterns for schema/enum discoveries

It does not cover:
- Live Firebase write-path verification (use API tests/live checks)
- Home Assistant runtime behavior debugging

## Tooling Requirements

Core APK tooling:
- `apkeep` (download from Google Play)
- `jadx` / `jadx-gui` (decompile APK)

Optional JS tooling:
- Node.js + npm
- Deobf helper: `.agents/skills/huckleberry-apk-reverse/scripts/deobf/`
- Script: `.agents/skills/huckleberry-apk-reverse/scripts/deobf/deobf-pretty.mjs`

Install commands:

```bash
# apkeep (requires Rust/Cargo)
cargo install apkeep

# JS deobf dependencies (one-time)
npm --prefix ".agents/skills/huckleberry-apk-reverse/scripts/deobf" install
```

## Recommended APK Workflow

1. Download reference + target APKs:

```bash
# Last unobfuscated reference (keep permanently)
apkeep -a com.huckleberry_labs.app -v 0.9.258 .

# Latest release discovery + fetch
apkeep -a com.huckleberry_labs.app -l .
apkeep -a com.huckleberry_labs.app@<latest_version> .
```

2. Decompile into versioned output folders:

```bash
# GUI mode (interactive exploration)
jadx-gui com.huckleberry_labs.app_0.9.258.apk

# CLI mode (repeatable automation)
jadx -d "jadx output <version>" "com.huckleberry_labs.app@<version>.apk"
```

3. Analyze in this order:
- `sources/` for backend operations, Firebase SDK usage, and model behavior
- `resources/res/values/strings.xml` for config constants and endpoints
- `resources/AndroidManifest.xml` for services/permissions/app architecture
- `resources/assets/www/` for UI payload assembly and enum wiring

4. Validate findings across multiple evidence points:
- feature implementation chunk where payload/options are assembled
- `main.*.js` constants table (enum identity + mapping consistency)
- unobfuscated 0.9.258 reference for name recovery when latest is obfuscated

## JS Deobfuscation Workflow (When Needed)

Preflight:

```bash
ls "jadx output <version>/resources/assets/www"
npm --prefix ".agents/skills/huckleberry-apk-reverse/scripts/deobf" install
```

Optional clean run:

```bash
# PowerShell
Get-ChildItem "jadx output <version>/resources/assets/www" -File -Filter "*.deobf.js" | Remove-Item -Force
Get-ChildItem "jadx output <version>/resources/assets/www" -File -Filter "*.deobf.pretty.js" | Remove-Item -Force
```

Run deobfuscation:

```bash
npm --prefix ".agents/skills/huckleberry-apk-reverse/scripts/deobf" run deobf -- "jadx output <version>/resources/assets/www" --parallel 10
npm --prefix ".agents/skills/huckleberry-apk-reverse/scripts/deobf" run deobf -- "jadx output <version>/resources/assets/www" --parallel 10 --recursive
```

Optional readability pass on raw chunks:

```bash
npx prettier --write --ignore-path "" "jadx output <version>/resources/assets/www/*.js"
```

## Script Behavior (`.agents/skills/huckleberry-apk-reverse/scripts/deobf/deobf-pretty.mjs`)

- Processes all `*.js` chunks except generated outputs
- Generates `*.deobf.js`
- Generates prettified `*.deobf.pretty.js`
- Skips files with existing output for incremental reruns
- Uses parallel workers (`--parallel`)
- Applies per-file timeout with fallback pretty-on-original output

Expected summary checks:
- `OK_PRETTY` should equal total chunk count (or near-equal with `SKIP_PRETTY_EXISTS`)
- `FAIL_PRETTY` should be `0`
- non-zero `TIMEOUT_DEOBF` is acceptable when fallback pretty succeeds

## Search Strategy

1. Search `*.deobf.js` first.
2. Fall back to original `*.js` if deobfuscation timed out or missed symbols.
3. Prefer symbol-based queries over obfuscated class names, e.g.:
   - `bottleType`
   - `lastBottle`
   - `prefs.`
   - `intervals`
   - `mode`
   - analytics IDs
   - translation keys
4. Confirm each critical claim in at least two independent artifacts.

## Findings Hygiene (Recommended)

- Record APK version and output folder for each claim
- Mark confidence as `verified`, `likely`, or `hypothesis`
- Keep copy/paste snippets minimal and reference exact symbol names
- Promote only cross-validated findings into AGENTS or API docs

## Limitations

- Firebase security rules are not directly visible in APK assets.
- Cloud Functions/server behavior is not present in decompiled output.
- Cordova/native bridge calls can hide behavior across JS and platform layers.
- Obfuscated releases may require 0.9.258 back-reference for naming clarity.
