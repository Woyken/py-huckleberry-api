# Huckleberry API

## Source of Truth

- Firebase payload findings and schema discoveries live in:
  - `src/huckleberry_api/firebase_types.py`
- Behavior and write/read logic live in:
  - `src/huckleberry_api/api.py`

## Critical Rules

1. **Validate values before adding/changing them**
   - For enums, modes, units, state values, keys, or option lists originating from the app/Firebase schema, validate against APK/Firebase evidence first.
   - Never add guessed values. If key or value cannot ve verified with decompiled sources or live data, it must not be added.

2. **Keep types strict**
   - Prefer explicit strict models and constrained literals.
   - Avoid loosening to broad `Any`/open dicts/lists.

3. **Use `uv` for Python commands**
   - Run tests and Python commands with `uv` (for example: `uv run pytest ...`).

4. **Keep discoveries near code**
   - Add new schema findings as comments/docstrings on the relevant classes/fields in `firebase_types.py`.

## Reverse-Engineering Workflow

- Use: `.agents/skills/huckleberry-apk-reverse/SKILL.md`
- Current decompilation context: `jadx output latest/`

## Live App / API Parity Workflow

- Use `.agents/skills/controlling-huckleberry/SKILL.md` to explicitly select the shared Windows/Termux accessibility route or the preserved Windows CDP route, within that backend's documented capabilities.
- Accessibility supports inspection, Home/Reports navigation and semantic node actions (including form controls). It has no screenshots or coordinate gestures, and backend-changing actions need the parity/observer preconditions. Do not infer CDP screenshot/DOM support on Termux or switch backends mid-investigation. The two backends' claims are independent, not unified locking.
- Backend parity requires an authorized observer and captured backend before-state; the skill's UI checkpoints are not Firebase snapshots. Preserve before/action/after evidence before fixture cleanup.

## Android UI Debugging

- Repository-only accessibility tooling lives in `tools/huckleberry-control/`.
- Read its README before live use. The bridge is a developer tool, not part of
  the Python API package or a replacement Huckleberry product.
- Enroll the exact device/profile and test child. Never silently fall back to a
  personal app, screenshots, coordinate input or backend writes.
- The accessibility tool maps semantic controls in `controls.json`; Save/delete/timer
  actions are technically available but require separate parity evidence and an
  authorized observer. UI state does not prove Firebase parity.
- Termux's opt-in acceptance runner is `tools/huckleberry-control/scripts/termux-smoke.mjs`;
  read its `references/termux-acceptance.md` guide first. It observes by default, never prepares
  the app, and requires separate navigation opt-in. Do not count timeout/recovery as a passed run.

## Maintenance

When discovering new payload structures or semantics:
- Update `src/huckleberry_api/firebase_types.py` first.
- Add/update tests as needed.
