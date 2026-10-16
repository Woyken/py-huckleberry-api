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

- Use `.agents/skills/controlling-huckleberry/SKILL.md` to start and control the Android app on Windows, inspect saved records, and collect UI evidence.
- Backend parity requires an authorized observer and captured backend before-state; the skill's UI checkpoints are not Firebase snapshots. Preserve before/action/after evidence before fixture cleanup.

## Maintenance

When discovering new payload structures or semantics:
- Update `src/huckleberry_api/firebase_types.py` first.
- Add/update tests as needed.
